import logging
import argparse
from amaranth import *

from ....gateware.pads import *
from ... import *


class MultibyteCounterSubtarget(Elaboratable):
    help = ""
    def __init__(self, in_fifo, count_cyc):
        self.in_fifo = in_fifo
        self.counter = Signal(12)
        self.count_cyc = count_cyc

    def elaborate(self, platform):
        m = Module()

        timer = Signal(range(self.count_cyc))
        
        with m.FSM():
            with m.State("WAIT"):
                with m.If(timer==0):
                    m.d.sync += timer.eq(self.count_cyc-2)
                    m.next = "COUNT"
                with m.Else():
                    m.d.sync += timer.eq(timer - 1)
            with m.State("COUNT"):
                m.d.sync += self.counter.eq(self.counter + 1)
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(Cat(self.counter[6:12],
                                               Const(3, unsigned(2))
                                               ))
                ]
                m.next = "COUNT-2"
            with m.State("COUNT-2"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(Cat(self.counter[0:6],
                                               Const(2, unsigned(2))))
                                               
                ]
                m.next = "WAIT"
        
        return m

class MultibyteCounterInterface:
    help = ""
    def __init__(self, interface):
        self.lower = interface
    async def read(self):
        return await self.lower.read()
    async def measure(self):
        from time import time
        start = time()
        received = 0
        while time() < (start + 10.0):
            for _ in range(50000):
                received += len((await self.lower.read()).tobytes())
        finish = time()
        print(f"received {received/(finish-start):.1f} bytes/second")

class MultibyteCounterApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "just count"
    description = """
    Just runs a counter on the FPGA and streams it back.
    """
    
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)
        count_cyc = self.derive_clock(clock_name="count_cyc",
                input_hz=target.sys_clk_freq, output_hz=2 * 1e6)
        subtarget = iface.add_subtarget(MultibyteCounterSubtarget(in_fifo=iface.get_in_fifo(), count_cyc=count_cyc))

    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return MultibyteCounterInterface(iface)

    
