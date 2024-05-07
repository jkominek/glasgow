import logging
from amaranth import *
from amaranth.lib import enum, wiring
from amaranth.lib.wiring import In, Out

from ... import *

__all__ = ["PinsSubtarget", "PinsInterface", "PinsApplet"]


class PinsChannel(wiring.Component):
    en: Out(1)
    out: Out(1)

    def __init__(self):
        super().__init__()
        self.out_r = Signal.like(self.out)
        self.en_r = Signal.like(self.en)

    def elaborate(self, platform):
        m = Module()

        m.d.sync += self.out.eq(self.out_r)
        m.d.sync += self.en.eq(self.en_r)

        return m

class PinsSubtarget(Elaboratable):
    class Command(enum.Enum):
        Disable = 0x00
        Enable = 0x01
        SetValue = 0x02
    def __init__(self, pads, out_fifo):
        self.pads = pads
        self.out_fifo = out_fifo
    def elaborate(self, platform):
        m = Module()
        m.submodules.chan = chan = PinsChannel()
        # WTF no idea what the rest of this does
        m.d.comb += [
            self.pads.pin_t.oe.eq(chan.en),
            self.pads.pin_t.o.eq(chan.out)
        ]
        command = Signal(self.Command)
        value_low = Signal.like(self.out_fifo.r_data)
        with m.FSM():
            with m.State("ReadCommand"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += command.eq(self.out_fifo.r_data)
                    m.next = "HandleCommand"
            with m.State("HandleCommand"):
                with m.If(command == self.Command.Disable):
                    m.d.sync += chan.en_r.eq(0)
                    m.next = "ReadCommand"
                with m.If(command == self.Command.Enable):
                    m.d.sync += chan.en_r.eq(1)
                    m.next = "ReadCommand"
                with m.If(command == self.Command.SetValue):
                    with m.If(self.out_fifo.r_rdy):
                        m.d.sync += chan.out_r.eq(self.out_fifo.r_data)
                        m.next = "ReadCommand"
        return m
            
                    
        
class PinsInterface:
    def __init__(self, interface, logger):
        self.lower = interface
        self._logger = logger
        self._level  = logging.DEBUG if self._logger.name == __name__ else logging.TRACE

    def _log(self, message, *args):
        self._logger.log(self._level, "pins: " + message, *args)

    async def enable(self, is_enabled=True):
        if is_enabled:
            self._log("enable")
            await self.lower.write([PinsSubtarget.Command.Enable.value])
        else:
            self._log("disable")
            await self.lower.write([PinsSubtarget.Command.Disable.value])
        await self.lower.flush()

    async def disable(self):
        await self.enable(False)

    async def set_value(self, value: bool):
        self._log(f"value={value}")
        await self.lower.write([
            PinsSubtarget.Command.SetValue.value,
            *value.to_bytes(1)
        ])
        await self.lower.flush()
        await self.enable()

    

class PinsApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "twiddle pins"
    description = """
    Just sets pins.
    """
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)
        access.add_pin_argument(parser, "pin", default=True)

    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)
        iface.add_subtarget(PinsSubtarget(
            pads=iface.get_pads(args, pins=("pin",)),
            out_fifo=iface.get_out_fifo(),
        ))

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return PinsInterface(iface, self.logger)
