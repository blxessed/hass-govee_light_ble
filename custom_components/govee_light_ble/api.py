import asyncio
import bleak_retry_connector
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak import (
    BleakClient,
    BLEDevice
)
from .const import WRITE_CHARACTERISTIC_UUID, READ_CHARACTERISTIC_UUID
from .api_utils import (
    LedPacketHead,
    LedPacketCmd,
    LedColorType,
    LedPacket,
    GoveeUtils
)

import logging
_LOGGER = logging.getLogger(__name__)


def _scale_value(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    """Scale value from one range to another while clamping to input bounds."""
    if in_max == in_min:
        return out_min
    value = max(min(value, in_max), in_min)
    ratio = (value - in_min) / (in_max - in_min)
    return out_min + ratio * (out_max - out_min)

class GoveeAPI:
    state: bool | None = None
    brightness: int | None = None
    color: tuple[int, ...] | None = None

    def __init__(self, ble_device: BLEDevice, update_callback, segmented: bool = False):
        self._conn = None
        self._ble_device = ble_device
        self._segmented = segmented
        self._packet_buffer = []
        self._client = None
        self._update_callback = update_callback
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._buffer_lock = asyncio.Lock()

    @property
    def address(self):
        return self._ble_device.address

    async def _ensureConnected(self):
        """ connects to a bluetooth device """
        if self._client is not None and self._client.is_connected:
            return None
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return None
            await self._connect()
    
    async def _connect(self):
        self._client = await bleak_retry_connector.establish_connection(BleakClient, self._ble_device, self.address)
        await self._client.start_notify(READ_CHARACTERISTIC_UUID, self._handleReceive)

    async def _transmitPacket(self, packet: LedPacket):
        """ transmit the actiual packet """
        #convert to bytes
        frame = await GoveeUtils.generateFrame(packet)
        #transmit to UUID
        await self._client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, frame, False)

    async def _handleRequest(self, packet: LedPacket):
        """ process received responses """
        match packet.cmd:
            case LedPacketCmd.POWER:
                self.state = packet.payload[0] == 0x01
            case LedPacketCmd.BRIGHTNESS:
                reported = packet.payload[0]
                if self._segmented:
                    scaled = _scale_value(reported, 0, 100, 0, 255)
                else:
                    # Legacy devices report 0-254; clamp to avoid leaking out of HA range
                    scaled = _scale_value(reported, 0, 254, 0, 255)
                self.brightness = int(round(max(0, min(255, scaled))))
            case LedPacketCmd.COLOR:
                red = packet.payload[1]
                green = packet.payload[2]
                blue = packet.payload[3]
                self.color = (red, green, blue)
            case LedPacketCmd.SEGMENT:
                red = packet.payload[2]
                green = packet.payload[3]
                blue = packet.payload[4]
                self.color = (red, green, blue)

    async def _handleReceive(self, characteristic: BleakGATTCharacteristic, frame: bytearray):
        """ receives packets async """
        if not await GoveeUtils.verifyChecksum(frame):
            raise Exception("transmission error, received packet with bad checksum")
        
        packet = LedPacket(
            head=frame[0],
            cmd=frame[1],
            payload=frame[2:-1]
        )
        #only requests are expected to send a response
        if packet.head == LedPacketHead.REQUEST:
            await self._handleRequest(packet)
            await self._update_callback()

    async def _preparePacket(self, cmd: LedPacketCmd, payload: bytes | list = b'', request: bool = False, repeat: int = 3):
        """ add data to transmission buffer """
        head = LedPacketHead.REQUEST if request else LedPacketHead.COMMAND
        packet = LedPacket(head, cmd, payload)
        async with self._buffer_lock:
            for _ in range(repeat):
                self._packet_buffer.append(packet)

    async def _clearPacketBuffer(self):
        """ clears the packet buffer """
        self._packet_buffer = []

    async def sendPacketBuffer(self):
        """ transmits all buffered data """
        async with self._send_lock:
            async with self._buffer_lock:
                if not self._packet_buffer:
                    return None
                packets = self._packet_buffer
                self._packet_buffer = []
            try:
                await self._ensureConnected()
                for packet in packets:
                    await self._transmitPacket(packet)
            except Exception:
                async with self._buffer_lock:
                    self._packet_buffer = packets + self._packet_buffer
                raise
            #not disconnecting seems to improve connection speed

    async def requestStateBuffered(self):
        """ adds a request for the current power state to the transmit buffer """
        await self._preparePacket(LedPacketCmd.POWER, request=True)

    async def requestBrightnessBuffered(self):
        """ adds a request for the current brightness state to the transmit buffer """
        await self._preparePacket(LedPacketCmd.BRIGHTNESS, request=True)

    async def requestColorBuffered(self):
        """ adds a request for the current color state to the transmit buffer """
        if self._segmented:
            #0x01 means first segment
            await self._preparePacket(LedPacketCmd.SEGMENT, b'\x01', request=True)
        else:
            #legacy devices
            await self._preparePacket(LedPacketCmd.COLOR, request=True)
    
    async def setStateBuffered(self, state: bool):
        """ adds the state to the transmit buffer """
        if self.state == state:
            return None #nothing to do
        #0x1 = ON, Ox0 = OFF
        await self._preparePacket(LedPacketCmd.POWER, [0x1 if state else 0x0])
        await self.requestStateBuffered()
    
    async def setBrightnessBuffered(self, brightness: int, *, force: bool = False):
        """ adds the brightness to the transmit buffer """
        if brightness is None:
            return None
        brightness = int(max(0, min(255, brightness)))
        if not force and self.brightness == brightness:
            return None #nothing to do
        device_max = 100 if self._segmented else 254
        payload = int(
            round(_scale_value(brightness, 0, 255, 0, device_max))
        )
        payload = max(0, min(device_max, payload))
        await self._preparePacket(LedPacketCmd.BRIGHTNESS, [payload])
        await self.requestBrightnessBuffered()
        
    async def setColorBuffered(self, red: int, green: int, blue: int, *, force: bool = False):
        """ adds the color to the transmit buffer """
        if not force and self.color == (red, green, blue):
            return None #nothing to do
        if self._segmented:
            segment_payload = [
                LedColorType.SEGMENTS,
                0x01,  # first segment
                red,
                green,
                blue,
                0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF  # ensure segments stay lit
            ]
            await self._preparePacket(LedPacketCmd.COLOR, segment_payload)
            # also push legacy single-color payloads for broader compatibility
            await self._preparePacket(LedPacketCmd.COLOR, [LedColorType.SINGLE, red, green, blue])
            await self._preparePacket(LedPacketCmd.COLOR, [LedColorType.LEGACY, red, green, blue])
        else:
            #legacy devices
            await self._preparePacket(LedPacketCmd.COLOR, [LedColorType.SINGLE, red, green, blue])
            await self._preparePacket(LedPacketCmd.COLOR, [LedColorType.LEGACY, red, green, blue])
        await self.requestColorBuffered()
