import logging
from amaranth import *
from amaranth.lib import enum, wiring
from amaranth.lib.wiring import In, Out

from ... import *

__all__ = ["BlinkySubtarget", "BlinkyInterface", "BlinkyApplet"]


class BlinkyChannel(wiring.Component):
    en: Out(1)
    on_time: In(range(4_294_967_295))
    off_time: In(range(4_294_967_295))
    out: Out(1)
    onOff: Out(1)

    def __init__(self):
        super().__init__()
        self.out_r = Signal.like(self.out)
        self.en_r = Signal.like(self.en)
        self.on_timer_r = Signal.like(self.on_time)
        self.off_timer_r = Signal.like(self.off_time)

    def elaborate(self, platform):
        print("clk", platform.default_clk_frequency)
        m = Module()

        m.d.sync += self.out.eq(self.out_r)
        m.d.sync += self.en.eq(self.en_r)

        with m.If(self.out_r):
            with m.If(self.on_timer_r == 0):
                m.d.sync += self.on_timer_r.eq(self.on_time)
                # on time is finished, flip
                m.d.sync += self.out_r.eq(0)
            with m.Else():
                m.d.sync += self.on_timer_r.eq(self.on_timer_r - 1)
        with m.Else():
            with m.If(self.off_timer_r == 0):
                m.d.sync += self.off_timer_r.eq(self.off_time)
                # off time is finished, flip
                m.d.sync += self.out_r.eq(1)
            with m.Else():
                m.d.sync += self.off_timer_r.eq(self.off_timer_r - 1)

        return m

class BlinkySubtarget(Elaboratable):
    class Command(enum.Enum):
        Disable = 0x00
        Enable = 0x01
        SetOnTime = 0x02
        SetOffTime = 0x03
    def __init__(self, pads, out_fifo):
        self.pads = pads
        self.out_fifo = out_fifo
    def elaborate(self, platform):
        m = Module()
        m.submodules.chan = chan = BlinkyChannel()
        # WTF no idea what the rest of this does
        m.d.comb += [
            self.pads.pin_t.oe.eq(chan.en),
            self.pads.pin_t.o.eq(chan.out)
        ]
        command = Signal(self.Command)
        valueA = Signal.like(self.out_fifo.r_data)
        valueB = Signal.like(self.out_fifo.r_data)
        valueC = Signal.like(self.out_fifo.r_data)
        valueD = Signal.like(self.out_fifo.r_data)
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
                with m.If(command == self.Command.SetOnTime):
                    m.d.comb += self.out_fifo.r_en.eq(1)
                    with m.If(self.out_fifo.r_rdy):
                        m.d.sync += valueA.eq(self.out_fifo.r_data)
                        m.next = "ReadOnTime-1"
                with m.If(command == self.Command.SetOffTime):
                    m.d.comb += self.out_fifo.r_en.eq(1)
                    with m.If(self.out_fifo.r_rdy):
                        m.d.sync += valueA.eq(self.out_fifo.r_data)
                        m.next = "ReadOffTime-1"
            with m.State("ReadOnTime-1"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += valueB.eq(self.out_fifo.r_data)
                    m.next = "ReadOnTime-2"
            with m.State("ReadOnTime-2"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += valueC.eq(self.out_fifo.r_data)
                    m.next = "ReadOnTime-3"
            with m.State("ReadOnTime-3"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += chan.on_time.eq(Cat(self.out_fifo.r_data, valueC, valueB, valueA))
                    m.next = "ReadCommand"
            with m.State("ReadOffTime-1"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += valueB.eq(self.out_fifo.r_data)
                    m.next = "ReadOffTime-2"
            with m.State("ReadOffTime-2"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += valueC.eq(self.out_fifo.r_data)
                    m.next = "ReadOffTime-3"
            with m.State("ReadOffTime-3"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += chan.off_time.eq(Cat(self.out_fifo.r_data, valueC, valueB, valueA))
                    m.next = "ReadCommand"
                
        return m
            
                    
        
class BlinkyInterface:
    def __init__(self, interface, logger):
        self.lower = interface
        self._logger = logger
        self._level  = logging.DEBUG if self._logger.name == __name__ else logging.TRACE

    def _log(self, message, *args):
        self._logger.log(self._level, "pins: " + message, *args)

    async def enable(self, is_enabled=True):
        if is_enabled:
            self._log("enable")
            await self.lower.write([BlinkySubtarget.Command.Enable.value])
        else:
            self._log("disable")
            await self.lower.write([BlinkySubtarget.Command.Disable.value])
        await self.lower.flush()

    async def disable(self):
        await self.enable(False)

    async def set_value(self, frequency=100, duty=50):
        if duty<0:
            duty = 0
        if duty>100:
            duty = 100
        period = 48000000.0 / frequency
        ontime = period * (duty / 100.0)
        offtime = period - ontime
        ontime = int(round(ontime))
        offtime = int(round(offtime))
        self._log(f"frequency={frequency}")
        await self.lower.write([
            BlinkySubtarget.Command.SetOnTime.value,
            *ontime.to_bytes(4)
        ])
        await self.lower.write([
            BlinkySubtarget.Command.SetOffTime.value,
            *offtime.to_bytes(4)
        ])
        await self.lower.flush()
        await self.enable()

    

class BlinkyApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "blink a pin"
    description = """
    Just blinks a pin, forever.
    """
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)
        access.add_pin_argument(parser, "pin", default=True)

    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)
        iface.add_subtarget(BlinkySubtarget(
            pads=iface.get_pads(args, pins=("pin",)),
            out_fifo=iface.get_out_fifo(),
        ))

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return BlinkyInterface(iface, self.logger)
