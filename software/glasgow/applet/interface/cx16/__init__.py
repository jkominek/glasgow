import logging
import argparse
from amaranth import *
import asyncio

from ....gateware.pads import *
from ....gateware.clockgen import *
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.fifo import AsyncFIFO
from ... import *

import sys
import pty
import os

class CommanderX16Subtarget(Elaboratable):
    help = ""
    def __init__(self, in_fifo, out_fifo, pads):
        self.in_fifo = in_fifo
        self.out_fifo = out_fifo
        self.pads = pads

    def elaborate(self, platform):
        m = Module()

        interrupt = Signal(reset=0)
        rdb = Signal()
        wrb = Signal()

        address = Signal(5, reset_less=True)
        readbus = Signal(8, reset_less=True)
        writebus = Signal(8, reset_less=True)

        m.submodules += [
            FFSynchronizer(self.pads.rdb_t.i, rdb),
            FFSynchronizer(self.pads.wrb_t.i, wrb),

            FFSynchronizer(Cat(self.pads.a0_t.i,
                               self.pads.a1_t.i,
                               self.pads.a2_t.i,
                               self.pads.a3_t.i,
                               self.pads.a4_t.i),
                           address),

            FFSynchronizer(Cat(self.pads.d0_t.i,
                               self.pads.d1_t.i,
                               self.pads.d2_t.i,
                               self.pads.d3_t.i,
                               self.pads.d4_t.i,
                               self.pads.d5_t.i,
                               self.pads.d6_t.i,
                               self.pads.d7_t.i),
                           writebus)
        ]

        m.d.comb += [
            # drive data lines only when RDB is active
            self.pads.d0_t.oe.eq(~self.pads.rdb_t.i),
            self.pads.d1_t.oe.eq(~self.pads.rdb_t.i),
            self.pads.d2_t.oe.eq(~self.pads.rdb_t.i),
            self.pads.d3_t.oe.eq(~self.pads.rdb_t.i),
            self.pads.d4_t.oe.eq(~self.pads.rdb_t.i),
            self.pads.d5_t.oe.eq(~self.pads.rdb_t.i),
            self.pads.d6_t.oe.eq(~self.pads.rdb_t.i),
            self.pads.d7_t.oe.eq(~self.pads.rdb_t.i),

            self.pads.d0_t.o.eq(readbus[0]),
            self.pads.d1_t.o.eq(readbus[1]),
            self.pads.d2_t.o.eq(readbus[2]),
            self.pads.d3_t.o.eq(readbus[3]),
            self.pads.d4_t.o.eq(readbus[4]),
            self.pads.d5_t.o.eq(readbus[5]),
            self.pads.d6_t.o.eq(readbus[6]),
            self.pads.d7_t.o.eq(readbus[7]),
            
            # interrupt signal controls whether
            # irqb is high-z or pulled low. (matching
            # behavior of open drain output, hopefully)
            self.pads.irqb_t.oe.eq(interrupt),
            self.pads.irqb_t.o.eq(0),

            # always read only
            self.pads.a0_t.oe.eq(0),
            self.pads.a1_t.oe.eq(0),
            self.pads.a2_t.oe.eq(0),
            self.pads.a3_t.oe.eq(0),
            self.pads.a4_t.oe.eq(0),

            self.pads.rdb_t.oe.eq(0),
            self.pads.wrb_t.oe.eq(0)
        ]

        with m.FSM():
            with m.State("Waiting"):
                with m.If(~rdb):
                    with m.If(address==0):
                        m.d.comb += self.out_fifo.r_en.eq(1)
                        with m.If(self.out_fifo.r_rdy):
                            m.d.sync += [
                                readbus.eq(self.out_fifo.r_data)
                            ]
                    with m.If(address==5):
                        m.d.sync += [
                            readbus.eq(Cat(self.out_fifo.r_rdy,
                                           Const(0, unsigned(0)),
                                           Const(0, unsigned(0)),
                                           Const(0, unsigned(0)),
                                           Const(0, unsigned(0)),
                                           self.in_fifo.w_rdy,
                                           self.in_fifo._fifo.w_level==0,
                                           Const(0, unsigned(0))))
                        ]
                    m.next = "Read"
                with m.If(~wrb):
                    m.next = "Write"

            with m.State("Read"):
                with m.If(rdb):
                    # if we wanted to do something after having
                    # read a byte, we could do it here
                    m.next = "Waiting"

            with m.State("Write"):
                with m.If(wrb):
                    # register 0
                    with m.If(address==0):
                        m.d.comb += [
                            self.in_fifo.w_en.eq(1),
                            self.in_fifo.w_data.eq(writebus)
                        ]
                    m.next = "Waiting"

        return m

class CommanderX16Interface:
    help = ""
    def __init__(self, interface):
        self.lower = interface
    async def read(self):
        return (await self.lower.read()).tobytes()
    async def display(self):
        while True:
            chunk = await self.read()
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            await self.lower.write(chunk)
            await self.lower.flush()
    async def pty(self):
        master, slave = pty.openpty()
        print(os.ttyname(slave))
        dev_fut = uart_fut = None
        while True:
            if dev_fut is None:
                dev_fut = asyncio.get_event_loop().run_in_executor(None, lambda: os.read(master, 1024))
            if uart_fut is None:
                uart_fut = asyncio.ensure_future(self.read())
            await asyncio.wait([uart_fut, dev_fut], return_when=asyncio.FIRST_COMPLETED)

            if dev_fut.done():
                data = await dev_fut
                dev_fut = None
                if not data:
                    break
                await self.lower.write(data)
                await self.lower.flush()
            if uart_fut.done():
                data = await uart_fut
                uart_fut = None
                os.write(master, data)
        for fut in [uart_fut, dev_fut]:
            if fut is not None and not fut.done():
                fut.cancel()

class CommanderX16Applet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "commander x16 interface"
    description = """
    Reads from an CommanderX16 and streams it back
    """

    __pins = ('irqb', 'rdb', 'wrb', 'a4', 'a3', 'a2', 'a1', 'a0',
              'd7', 'd6', 'd5', 'd4', 'd3', 'd2', 'd1', 'd0')
    
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

        for idx, pin in enumerate(cls.__pins):
            access.add_pin_argument(parser, pin, default=idx)

    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)
        subtarget = iface.add_subtarget(
            CommanderX16Subtarget(in_fifo=iface.get_in_fifo(),
                                  out_fifo=iface.get_out_fifo(),
                                  pads=iface.get_pads(args, pins=self.__pins))
        )
        
    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return CommanderX16Interface(iface)

    
