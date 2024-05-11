import logging
import argparse
from amaranth import *

from ....gateware.pads import *
from ... import *


class SimpleCounterSubtarget(Elaboratable):
    help = ""
    def __init__(self, in_fifo):
        self.in_fifo = in_fifo
        self.counter = Signal(8)

    def elaborate(self, platform):
        m = Module()

        m.d.sync += self.counter.eq(self.counter + 1)
        m.d.comb += [
            self.in_fifo.w_en.eq(1),
            self.in_fifo.w_data.eq(self.counter)
        ]
        
        return m

class SimpleCounterInterface:
    help = ""
    def __init__(self, interface):
        self.lower = interface
    async def read(self):
        return await self.lower.read()

class SimpleCounterApplet(GlasgowApplet):
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
        subtarget = iface.add_subtarget(SimpleCounterSubtarget(in_fifo=iface.get_in_fifo()))

    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return SimpleCounterInterface(iface)
