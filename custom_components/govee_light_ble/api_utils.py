from enum import IntEnum
from dataclasses import dataclass

class LedPacketHead(IntEnum):
    COMMAND = 0x33
    REQUEST = 0xaa

class LedPacketCmd(IntEnum):
    POWER      = 0x01
    BRIGHTNESS = 0x04
    COLOR      = 0x05
    SEGMENT    = 0xa5

class LedColorType(IntEnum):
    SEGMENTS    = 0x15
    SINGLE      = 0x02
    LEGACY      = 0x0D

@dataclass
class LedPacket:
    #request data or perform a change
    head: LedPacketHead
    #data to request or command to perform
    cmd: LedPacketCmd
    #actual data to transmit
    payload: bytes | list = b''

class GoveeUtils:
    @staticmethod
    async def generateChecksum(frame: bytes):
        """ returns checksum by XORing all data bytes """
        checksum = 0
        for b in frame:
            checksum ^= b
        #pad response to 8 bits
        return bytes([checksum & 0xFF])

    @staticmethod
    async def generateFrame(packet: LedPacket):
        """ returns transmittable frame bytes """
        #pad cmd to 8 bits
        cmd = packet.cmd & 0xFF
        #combine segments
        if isinstance(packet.payload, (bytes, bytearray)):
            payload_bytes = bytes(packet.payload)
        else:
            payload_bytes = bytes(
                int(max(0, min(255, round(value)))) for value in packet.payload
            )
        frame = bytes([packet.head, cmd]) + payload_bytes
        #pad frame data to 19 bytes (plus checksum)
        frame += bytes([0] * (19 - len(frame)))
        #add checksum to end
        frame += await GoveeUtils.generateChecksum(frame)
        return frame

    @staticmethod
    async def verifyChecksum(frame: bytes):
        checksum_received = frame[-1].to_bytes(1, 'big')
        checksum_calculated = await GoveeUtils.generateChecksum(frame[:-1])
        return checksum_received == checksum_calculated
