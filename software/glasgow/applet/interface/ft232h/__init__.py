import logging
import argparse
from amaranth import *
import asyncio

from ....gateware.pads import *
from ....gateware.clockgen import *
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.fifo import AsyncFIFO
from ....gateware.clockgen import ClockGen
from ... import *

import sys
import pty
import os

class FT232HAsyncFIFOSubtarget(Elaboratable):
    help = ""
    def __init__(self, in_fifo, out_fifo, pads):
        self.in_fifo = in_fifo
        self.out_fifo = out_fifo
        self.pads = pads

    def elaborate(self, platform):
        m = Module()

        rxfb = Signal()
        txeb = Signal()
        rdb = Signal(reset=1)
        wrb = Signal(reset=1)

        outgoing = Signal(reset=0)

        readbus = Signal(8, reset_less=True)
        writebus = Signal(8, reset_less=True)

        m.submodules += [
            FFSynchronizer(self.pads.rxfb_t.i, rxfb),
            FFSynchronizer(self.pads.txeb_t.i, txeb),

            FFSynchronizer(Cat(self.pads.d0_t.i,
                               self.pads.d1_t.i,
                               self.pads.d2_t.i,
                               self.pads.d3_t.i,
                               self.pads.d4_t.i,
                               self.pads.d5_t.i,
                               self.pads.d6_t.i,
                               self.pads.d7_t.i), readbus)
        ]
        
        m.d.comb += [
            # drive data lines only when WRB is active
            self.pads.d0_t.oe.eq(outgoing),
            self.pads.d1_t.oe.eq(outgoing),
            self.pads.d2_t.oe.eq(outgoing),
            self.pads.d3_t.oe.eq(outgoing),
            self.pads.d4_t.oe.eq(outgoing),
            self.pads.d5_t.oe.eq(outgoing),
            self.pads.d6_t.oe.eq(outgoing),
            self.pads.d7_t.oe.eq(outgoing),

            self.pads.d0_t.o.eq(writebus[0]),
            self.pads.d1_t.o.eq(writebus[1]),
            self.pads.d2_t.o.eq(writebus[2]),
            self.pads.d3_t.o.eq(writebus[3]),
            self.pads.d4_t.o.eq(writebus[4]),
            self.pads.d5_t.o.eq(writebus[5]),
            self.pads.d6_t.o.eq(writebus[6]),
            self.pads.d7_t.o.eq(writebus[7]),
            
            self.pads.rxfb_t.oe.eq(0),
            self.pads.txeb_t.oe.eq(0),

            self.pads.rdb_t.oe.eq(1),
            self.pads.rdb_t.o.eq(rdb),

            self.pads.wrb_t.oe.eq(1),
            self.pads.wrb_t.o.eq(wrb)
        ]

        with m.FSM():
            with m.State("Waiting"):
                with m.If((~rxfb) & self.in_fifo.w_rdy):
                    # we can move a byte from ft232h to our fifo
                    m.d.sync += [
                        rdb.eq(0)
                    ]
                    m.next = "FT232ToHost-0"
                with m.Elif((~txeb) & self.out_fifo.r_rdy):
                    # we can move a byte from our fifo to the ft232h
                    m.d.comb += [
                        self.out_fifo.r_en.eq(1)
                    ]
                    m.d.sync += [
                        outgoing.eq(1),
                        writebus.eq(self.out_fifo.r_data)
                    ]
                    m.next = "HostToFT232-0"
            with m.State("FT232ToHost-0"):
                m.next = "FT232ToHost-1"
            with m.State("FT232ToHost-1"):
                m.next = "FT232ToHost-2"
            with m.State("FT232ToHost-2"):
                m.next = "FT232ToHost-3"
            with m.State("FT232ToHost-3"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(readbus)
                ]
                m.d.sync += [
                    rdb.eq(1)
                ]
                m.next = "WaitForRXFHigh"
            with m.State("WaitForRXFHigh"):
                with m.If(rxfb):
                    m.next = "Waiting"
            with m.State("HostToFT232-0"):
                m.next = "HostToFT232-1"
            with m.State("HostToFT232-1"):
                m.d.sync += [
                    wrb.eq(0)
                ]
                m.next = "HostToFT232-2"
            with m.State("HostToFT232-2"):
                m.next = "HostToFT232-3"
            with m.State("HostToFT232-3"):
                m.d.sync += [
                    outgoing.eq(1),
                    wrb.eq(1)
                ]
                m.next = "WaitForTXEHigh"
            with m.State("WaitForTXEHigh"):
                with m.If(txeb):
                    m.next = "Waiting"

        return m

class FT232HAsyncFIFOInterface:
    help = ""
    def __init__(self, interface):
        self.lower = interface
    async def read(self):
        return (await self.lower.read()).tobytes()
    async def write(self, stuff):
        await self.lower.write(stuff)
        await self.lower.flush()

class FT232HAsyncFIFOApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "ft232h async fifo interface"
    description = """
    connects a FT232H Async FIFO to the glasgow FIFOs.

    why would you do this? to make sure you've understood the
    interface.
    """

    __pins = ('d0', 'd1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7',
              'rxfb', 'txeb', 'rdb', 'wrb')
    
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

        for idx, pin in enumerate(cls.__pins):
            access.add_pin_argument(parser, pin, default=idx)

    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)
        subtarget = iface.add_subtarget(
            FT232HAsyncFIFOSubtarget(in_fifo=iface.get_in_fifo(),
                                     out_fifo=iface.get_out_fifo(),
                                     pads=iface.get_pads(args, pins=self.__pins))
        )
        
    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return FT232HAsyncFIFOInterface(iface)

    
