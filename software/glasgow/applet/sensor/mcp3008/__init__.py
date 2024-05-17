import logging
import argparse
from amaranth import *
import asyncio

from ....gateware.pads import *
from ....gateware.clockgen import *
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.fifo import AsyncFIFO
from ... import *


class MCP3008Subtarget(Elaboratable):
    help = ""
    def __init__(self, in_fifo, pads):
        self.in_fifo = in_fifo
        self.pads = pads

    def elaborate(self, platform):
        m = Module()

        miso = Signal()
        mosi = Signal(reset=0)
        cs = Signal(reset=1)
        sclk = Signal(reset=0)

        m.submodules.adcfifo = adcfifo = AsyncFIFO(width=13, depth=4, r_domain="sync", w_domain="fsm")
        m.submodules += [
            FFSynchronizer(self.pads.miso_t.i, miso)
        ]

        timer = Signal(range(32))
        timer_cyc = Signal.like(timer)
        
        channel = Signal(3)
        curchannel = Signal.like(channel)
        value = Signal(10)

        m.d.comb += [
            timer_cyc.eq(13),
            
 #           self.pads.miso_t.oe.eq(0),

            self.pads.mosi_t.oe.eq(1),
            self.pads.mosi_t.o.eq(mosi),

            self.pads.cs_t.oe.eq(1),
            self.pads.cs_t.o.eq(cs),

            self.pads.sclk_t.oe.eq(1),
            self.pads.sclk_t.o.eq(sclk)
        ]

        m.d.sync += timer.eq(timer-1)
        with m.FSM() as fsm:
            with m.State("Low"):
                with m.If(timer==0):
                    m.d.sync += [
                        sclk.eq(1),
                        timer.eq(timer_cyc)
                    ]
                    m.next = "High"
            with m.State("High"):
                with m.If(timer==0):
                    m.d.sync += [
                        sclk.eq(0),
                        timer.eq(timer_cyc)
                    ]
                    m.next = "Low"

        m.domains.fsm = ClockDomain("fsm", clk_edge="neg")
        m.d.comb += [
            ClockSignal("fsm").eq(sclk)
        ]
        with m.FSM(domain="fsm"):
            with m.State("AcquireWait"):
                m.d.fsm += [
                    cs.eq(0),
                    mosi.eq(1),
                    curchannel.eq(channel),
                    channel.eq(channel+1)
                ]
                m.next = "SingleDiff"
            with m.State("SingleDiff"):
                m.d.fsm += mosi.eq(1) # single
                m.next = "Channel2"
            with m.State("Channel2"):
                m.d.fsm += mosi.eq(curchannel[2])
                m.next = "Channel1"
            with m.State("Channel1"):
                m.d.fsm += mosi.eq(curchannel[1])
                m.next = "Channel0"
            with m.State("Channel0"):
                m.d.fsm += mosi.eq(curchannel[0])
                m.next = "DontCare"
            with m.State("DontCare"):
                # here the ADC doesn't care what we send,
                # and isn't sending anything either
                m.d.fsm += mosi.eq(0)
                m.next = "NullBit"
            with m.State("NullBit"):
                # and here it'll send a 0 every time.
                # if you're smart you'll put a weak
                # pullup on MISO so that you can tell
                # when it sends this.
                m.next = "ReadData-9"
            for i in range(1,10):
                with m.State(f"ReadData-{i}"):
                    m.d.fsm += [
                        value.eq(Cat(miso, value[0:9]))
                    ]
                    m.next = f"ReadData-{i-1}"
            with m.State("ReadData-0"):
                m.d.comb += [
                    adcfifo.w_en.eq(1),
                    adcfifo.w_data.eq(Cat(miso, value[0:9], curchannel))
                ]
                m.d.fsm += [
                    cs.eq(1)
                ]
                m.next = "AcquireWait"

        # read values from the asyncfifo into our fast clockdomain
        # and send them out over USB.
        fastvalue = Signal(13)
        with m.FSM():
            with m.State("ReadValue"):
                m.d.comb += adcfifo.r_en.eq(1)
                with m.If(adcfifo.r_rdy):
                    m.d.sync += fastvalue.eq(adcfifo.r_data)
                    m.next = "HandleValue-High"
            with m.State("HandleValue-High"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(Cat(fastvalue[7:13], Const(2, unsigned(2))))
                ]
                m.next = "HandleValue-Low"
            with m.State("HandleValue-Low"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(Cat(fastvalue[0:7], Const(0)))
                ]
                m.next = "ReadValue"

        return m

import time
class MCP3008Interface:
    help = ""
    def __init__(self, interface):
        self.lower = interface
        self.data = b''
        self.values = [0] * 8
        self.prevtime = time.time()
        self.cnt = 0
        self.Hz = 0.0
    async def read(self):
        results = [ ]

        self.data += (await self.lower.read()).tobytes()
        for i in range(len(self.data)):
            if self.data[i] & 0x80 and (i+1)<len(self.data):
                channel = (self.data[i] & 0x38)>>3
                topbits = self.data[i] & 0x07
                val = (topbits << 7) | (self.data[i+1] & 0x7f)
                self.values[channel] = val
                if channel == 7:
                    if (self.cnt % 20000) == 0:
                        now = time.time()
                        self.Hz = 20000 / (now - self.prevtime)
                        self.prevtime = now
                    results.append([self.cnt] + self.values)
                    self.cnt += 1

        if len(self.data)>0 and (self.data[-1] & 0x80):
            self.data = self.data[-1:]
        else:
            self.data = b''

        return results
    async def display(self):
        while True:
            results = await self.read()
            for record in results:
                print("{: 6d}  {:4d} {:4d} {:4d} {:4d} {:4d} {:4d} {:4d} {:4d}".format(*record))
    async def record(self):
        import datetime
        print("capturing...")
        f = open(datetime.datetime.now().isoformat()+".csv", "w")
        cnt = 0
        try:
            while True:
                results = await self.read()
                for record in results:
                    f.write(",".join(map(str, record))+"\n")
                cnt += len(results)
        except asyncio.CancelledError:
            print(f"captured {cnt} records")
        f.close()
            

class MCP3008Applet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "stream ADC data"
    description = """
    Reads from an MCP3008 and streams it back
    """

    __pins = ('cs', 'sclk', 'mosi', 'miso')
    
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

        for pin in cls.__pins:
            access.add_pin_argument(parser, pin, default=False)

    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)
        subtarget = iface.add_subtarget(
            MCP3008Subtarget(in_fifo=iface.get_in_fifo(),
                             pads=iface.get_pads(args, pins=self.__pins))
        )
        
    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return MCP3008Interface(iface)

    
