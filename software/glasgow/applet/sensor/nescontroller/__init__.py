import logging
import argparse
from amaranth import *

from ....gateware.pads import *
from ... import *

class NESControllerSubtarget(Elaboratable):
    help = ""
    def __init__(self, in_fifo, out_fifo, pads):
        self.in_fifo = in_fifo
        self.out_fifo = out_fifo
        self.pads = pads

    def elaborate(self, platform):
        m = Module()

        latch_timer = Signal(range(800000), reset=0)
        clock_timer = Signal(range(600), reset=0)

        latch = Signal(reset=0)
        clock = Signal(reset=1)
        data = Signal()

        m.d.comb += [
            self.pads.latch_t.oe.eq(1),
            self.pads.latch_t.o.eq(latch),

            self.pads.clock_t.oe.eq(1),
            self.pads.clock_t.o.eq(clock),

            data.eq(self.pads.data_t.i)
        ]

        buttons = Signal(14)
        read_counter = Signal(range(14))

        with m.If(latch_timer != 0):
            m.d.sync += latch_timer.eq(latch_timer - 1)
        with m.Else():
            m.d.sync += latch_timer.eq(int(platform.default_clk_frequency/60))

        with m.If(clock_timer != 0):
            m.d.sync += clock_timer.eq(clock_timer - 1)
        with m.Else():
            m.d.sync += clock_timer.eq(int(platform.default_clk_frequency/(2*80000)))

        with m.FSM() as fsm:
            with m.State("LATCH-WAIT"):
                with m.If(latch_timer == 0):
                    m.d.sync += clock.eq(1)
                    m.next = "LATCH-START-WAIT"
            with m.State("LATCH-START-WAIT"):
                with m.If(clock_timer == 0):
                    m.d.sync += [
                        latch.eq(1),
                        read_counter.eq(14)
                    ]
                    m.next = "LATCHING"
            with m.State("LATCHING"):
                with m.If(clock_timer == 0):
                    m.d.sync += [
                        latch.eq(0),
                        clock.eq(0)
                    ]
                    # first bit is out now
                    m.next = "READ-DATA"
            with m.State("READ-DATA"):
                with m.If(clock_timer == 0):
                    m.d.sync += [
                        buttons.eq(Cat(data, buttons[0:13])),
                        clock.eq(1),
                        read_counter.eq(read_counter - 1)
                    ]
                    m.next = "READ-WAIT"
            with m.State("READ-WAIT"):
                with m.If(read_counter == 0):
                    m.next = "SEND-BUTTONS-1"
                with m.Else():
                    with m.If(clock_timer == 0):
                        m.d.sync += clock.eq(0)
                        m.next = "READ-DATA"
            with m.State("SEND-BUTTONS-1"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(Cat(buttons[7:14], Const(1)))
                ]
                m.next = "SEND-BUTTONS-2"
            with m.State("SEND-BUTTONS-2"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(Cat(buttons[0:7], Const(0)))
                ]
                m.next = "LATCH-WAIT"

        return m

class NESControllerInterface:
    help = ""
    def __init__(self, interface, logger):
        self.lower = interface
        self.logger = logger
    async def read(self):
        return await self.lower.read()
    async def watch(self):
        buf = b''
        while True:
            buf += await self.lower.read()
            buf = buf[-3:]
            if len(buf)<3:
                # accumulate more
                continue
            if not (buf[0] & 0x80):
                # not a start byte
                buf = buf[1:]
            # now a start byte
            ba = buf[0] & 0x7f
            bb = buf[1] & 0x7f
            ba = 0b11111111 - ba
            bb = 0b11111111 - bb
            buttons = [ ]
            # this button mapping is SNES
            if ba & 0x40:
                buttons.append("B")
            if ba & 0x20:
                buttons.append("Y")
            if ba & 0x10:
                buttons.append("select")
            if ba & 0x08:
                buttons.append("start")
            if ba & 0x04:
                buttons.append("up")
            if ba & 0x02:
                buttons.append("down")
            if ba & 0x01:
                buttons.append("left")
            if bb & 0x40:
                buttons.append("right")
            if bb & 0x20:
                buttons.append("A")
            if bb & 0x10:
                buttons.append("X")
            if bb & 0x08:
                buttons.append("LR")
            if bb & 0x04:
                buttons.append("RR")
            print(" ".join(buttons))


class NESControllerApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "NES/SNES Controller"
    description = """
    Read a NES or SNES controller
    """

    __pins = ("clock", "latch", "data")
    
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

        for pin in cls.__pins:
            access.add_pin_argument(parser, pin, default=True)

        parser.add_argument(
            "-s", "--scan", metavar="SCANFREQ", type=int, default=60,
            help="set controller scan frequency, Hz (default: %(default)s")
        parser.add_argument(
            "-c", "--clock", metavar="CLOCKFREQ", type=int, default=80000,
            help="set shift register clock frequency, Hz (default: %(default)s")

    def build(self, target, args):
        self.mux_interface = iface = \
            target.multiplexer.claim_interface(self, args)
        # todo add frequencies later
        subtarget = iface.add_subtarget(NESControllerSubtarget(
            in_fifo=iface.get_in_fifo(),
            out_fifo=iface.get_out_fifo(),
            pads=iface.get_pads(args, pins=self.__pins)
        ))

    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return NESControllerInterface(iface, self.logger)
