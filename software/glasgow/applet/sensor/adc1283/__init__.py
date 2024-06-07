import logging
import argparse
from amaranth import *
import asyncio

from ....gateware.pads import *
from ....gateware.clockgen import *
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.fifo import AsyncFIFO
from ... import *


class ADC1283Subtarget(Elaboratable):
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

        m.submodules.adcfifo = adcfifo = AsyncFIFO(width=15, depth=128, r_domain="sync", w_domain="fsm")
        m.submodules += [
            FFSynchronizer(self.pads.miso_t.i, miso)
        ]

        timer = Signal(range(32))
        timer_cyc = Signal.like(timer)
        
        nextchannel = Signal(3)
        curchannel = Signal.like(nextchannel)
        value = Signal(12)

        m.d.comb += [
            timer_cyc.eq(7),
            
 #           self.pads.miso_t.oe.eq(0),

            self.pads.mosi_t.oe.eq(1),
            self.pads.mosi_t.o.eq(mosi),

            self.pads.cs_t.oe.eq(1),
            self.pads.cs_t.o.eq(cs),

            self.pads.sclk_t.oe.eq(1),
            self.pads.sclk_t.o.eq(sclk & ~cs)
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
            with m.State("Loop"):
                m.d.fsm += [
                    cs.eq(0),
                    mosi.eq(0),
                    curchannel.eq(nextchannel),
                    nextchannel.eq(nextchannel+1),
                ]
                with m.If(nextchannel>0):
                    m.d.comb += [
                        adcfifo.w_en.eq(1),
                        adcfifo.w_data.eq(Cat(miso, value[0:11], curchannel))
                    ]
                m.next = "Wait"
            with m.State("Wait"):
                m.d.fsm += [
                    mosi.eq(0)
                ]
                m.next = "Channel2"
            with m.State("Channel2"):
                m.d.fsm += [
                    mosi.eq(nextchannel[2])
                ]
                m.next = "Channel1"
            with m.State("Channel1"):
                m.d.fsm += [
                    mosi.eq(nextchannel[1]),
                    value.eq(Cat(miso, value[0:11]))
                ]
                m.next = "Channel0"
            with m.State("Channel0"):
                m.d.fsm += [
                    mosi.eq(nextchannel[0]),
                    value.eq(Cat(miso, value[0:11]))
                ]
                m.next = "Read-10"
            for bit in range(1,11):
                with m.State(f"Read-{bit}"):
                    m.d.fsm += [
                        mosi.eq(0),
                        value.eq(Cat(miso, value[0:11]))
                    ]
                    m.next = f"Read-{bit-1}"
            with m.State("Read-0"):
                m.d.fsm += [
                    mosi.eq(0),
                    value.eq(Cat(miso, value[0:11]))
                ]
                with m.If(nextchannel == 0):
                    m.next = "Final"
                with m.Else():
                    m.next = "Loop"
            with m.State("Final"):
                m.d.comb += [
                    adcfifo.w_en.eq(1),
                    adcfifo.w_data.eq(Cat(miso, value[0:11], curchannel))
                ]
                m.d.fsm += [
                    cs.eq(1)
                ]
                m.next = "Loop"

        SevenD = Const(0x7d, unsigned(8))
        SevenE = Const(0x7e, unsigned(8))
        # read values from the asyncfifo into our fast clockdomain
        # and send them out over USB.
        fastvalue = Signal(15)
        # TODO i don't think all of these m.If(self.in_fifo.w_rdy) are
        # right, i think that needs to happen in a state where we're
        # not emitting bytes.
        with m.FSM():
            with m.State("ReadValue"):
                m.d.comb += adcfifo.r_en.eq(1)
                with m.If(adcfifo.r_rdy):
                    m.d.sync += fastvalue.eq(adcfifo.r_data)
                    m.d.comb += [
                        self.in_fifo.w_en.eq(1),
                        self.in_fifo.w_data.eq(SevenE)
                    ]
                    with m.If(self.in_fifo.w_rdy):
                        m.next = "CheckValue-High"
            with m.State("CheckValue-High"):
                with m.If((fastvalue[8:15] == SevenE) | (fastvalue[8:15] == SevenD)):
                    m.d.comb += [
                        self.in_fifo.w_en.eq(1),
                        self.in_fifo.w_data.eq(SevenD)
                    ]
                    with m.If(self.in_fifo.w_rdy):
                        m.next = "EmitFlipped-High"
                with m.Else():
                    m.d.comb += [
                        self.in_fifo.w_en.eq(1),
                        self.in_fifo.w_data.eq(Cat(fastvalue[8:15], Const(0, unsigned(1))))
                    ]
                    with m.If(self.in_fifo.w_rdy):
                        m.next = "CheckValue-Low"
            with m.State("EmitFlipped-High"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(Cat(fastvalue[8:15] ^ 0x20, Const(0, unsigned(1))))
                ]
                with m.If(self.in_fifo.w_rdy):
                    m.next = "CheckValue-Low"
            with m.State("CheckValue-Low"):
                with m.If((fastvalue[0:8] == SevenE) | (fastvalue[0:8] == SevenD)):
                    m.d.comb += [
                        self.in_fifo.w_en.eq(1),
                        self.in_fifo.w_data.eq(SevenD)
                    ]
                    with m.If(self.in_fifo.w_rdy):
                        m.next = "EmitFlipped-Low"
                with m.Else():
                    m.d.comb += [
                        self.in_fifo.w_en.eq(1),
                        self.in_fifo.w_data.eq(fastvalue[0:8])
                    ]
                    with m.If(self.in_fifo.w_rdy):
                        m.next = "ReadValue"
            with m.State("EmitFlipped-Low"):
                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(fastvalue[0:8] ^ 0x20)
                ]
                with m.If(self.in_fifo.w_rdy):
                    m.next = "ReadValue"

        return m

import time
class ADC1283Interface:
    help = ""
    def __init__(self, interface):
        self.lower = interface
        self.data = b''
        self.values = [0] * 8
        self.prevtime = time.time()
        self.cnt = 0
        self.Hz = 0.0
        self.result = [0]*8
    async def read(self):
        self.data += (await self.lower.read()).tobytes()

        results = [ ]

        while True:
            while len(self.data)>0 and self.data[0] != 0x7e:
                self.data = self.data[1:]

            if len(self.data)<5:
                break
            # drop 0x7e
            self.data = self.data[1:]

            if self.data[0] == 0x7d:
                self.data = bytes({self.data[1] ^ 0x20}) + self.data[2:]
            if self.data[1] == 0x7d:
                self.data = self.data[0:1] + bytes({self.data[2] ^ 0x20}) + self.data[3:]

            #print(self.data[0:2])
            bh = self.data[0]
            bl = self.data[1]

            channel = (bh >> 4) & 0x07
            val = (bh & 0x0f) << 8
            val |= bl

            self.data = self.data[2:]

            self.result[channel] = val
            
            if channel==7:
                results.append([self.cnt]+self.result)
                self.result = [0]*8
                self.cnt += 1
                #result = [0] * 8
                if (self.cnt % 20000) == 0:
                    now = time.time()
                    self.Hz = 20000 / (now - self.prevtime)
                    self.prevtime = now

        return results
    async def display(self):
        while True:
            results = await self.read()
            for record in results:
                print("{: 6d}  {:4d} {:4d} {:4d} {:4d} {:4d} {:4d} {:4d} {:4d}  {:.1f}".format(*record, self.Hz))
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
            

class ADC1283Applet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "stream ADC data"
    description = """
    Reads from an ADC1283 and streams it back
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
            ADC1283Subtarget(in_fifo=iface.get_in_fifo(),
                             pads=iface.get_pads(args, pins=self.__pins))
        )
        
    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return ADC1283Interface(iface)

    
