import logging
import argparse
from amaranth import *

from ....gateware.pads import *
from ... import *

import evdev

class NESSubtarget(Elaboratable):
    help = ""
    def __init__(self, out_fifo, pads):
        self.out_fifo = out_fifo
        self.pads = pads

    def elaborate(self, platform):
        m = Module()

        latch = Signal()
        clock = Signal()
        data = Signal()

        m.d.comb += [
            latch.eq(self.pads.latch_t.i),

            clock.eq(self.pads.clock_t.i),

            self.pads.data_t.oe.eq(1),
            self.pads.data_t.o.eq(data)
        ]

        # inputs to the shift register; what will be brought in
        # on the next latch event
        buttons = Signal(14)
        # shift register contents
        register = Signal.like(buttons)

        # holding spot for bytes
        command = Signal.like(self.out_fifo.r_data)

        # data is always the top bit of the shift register
        m.d.comb += data.eq(register[13])

        latch_deglitch = Signal(range(8))
        with m.FSM() as fsm:
            with m.State("LATCH-LOW"):
                with m.If(latch == 1):
                    with m.If(latch_deglitch>=4):
                        m.next = "LATCH-HIGH"
                    with m.Else():
                        m.d.sync += latch_deglitch.eq(latch_deglitch+1)
                with m.Else():
                    m.d.sync += latch_deglitch.eq(0)
            with m.State("LATCH-HIGH"):
                m.d.sync += register.eq(buttons)
                with m.If(latch == 0):
                    with m.If(latch_deglitch>=4):
                        m.next = "LATCH-LOW"
                    with m.Else():
                        m.d.sync += latch_deglitch.eq(latch_deglitch+1)
                with m.Else():
                    m.d.sync += latch_deglitch.eq(0)

        clock_deglitch = Signal(range(8))
        with m.FSM() as fsm:
            with m.State("CLOCK-HIGH"):
                with m.If(clock == 0):
                    with m.If(clock_deglitch>=4):
                        m.next = "CLOCK-LOW"
                    with m.Else():
                        m.d.sync += clock_deglitch.eq(clock_deglitch+1)
                with m.Else():
                    m.d.sync += clock_deglitch.eq(0)
            with m.State("CLOCK-LOW"):
                with m.If(clock == 1):
                    with m.If(clock_deglitch>=4):
                        m.d.sync += [
                            register.eq(Cat(Const(0), register[0:13]))
                        ]
                        m.next = "CLOCK-HIGH"
                    with m.Else():
                        m.d.sync += clock_deglitch.eq(clock_deglitch+1)
                with m.Else():
                    m.d.sync += clock_deglitch.eq(0)

        # reading from fifo into buttons
        with m.FSM():
            with m.State("READ-COMMAND"):
                m.d.comb += self.out_fifo.r_en.eq(1)
                with m.If(self.out_fifo.r_rdy):
                    m.d.sync += command.eq(self.out_fifo.r_data)
                    m.next = "PROCESS-COMMAND"
            with m.State("PROCESS-COMMAND"):
                with m.If(command[7] == 1):
                    # high byte
                    m.d.sync += buttons[7:14].eq(command[0:7])
                with m.Else():
                    # low byte
                    m.d.sync += buttons[0:7].eq(command[0:7])
                m.next = "READ-COMMAND"

        return m

class NESInterface:
    help = ""
    def __init__(self, interface, logger):
        self.lower = interface
        self.logger = logger
    async def emulate(self, path):
        device = evdev.InputDevice(path)
        # this works with my logitech gamepad F310
        mapping = { 304: 'A',
                    305: 'B',
                    307: 'X',
                    308: 'Y',
                    314: 'select',
                    315: 'start',
                    310: 'LR',
                    311: 'RR'
                   }
        bits = { 'B': 0x4000,
                 'Y': 0x2000,
                 'select': 0x1000,
                 'start': 0x0800,
                 'up': 0x0400,
                 'down': 0x0200,
                 'left': 0x0100,
                 # skipping 0x0080
                 'right': 0x0040,
                 'A': 0x0020,
                 'X': 0x0010,
                 'LR': 0x0008,
                 'RR': 0x0004
                }
        names = ('A', 'B', 'X', 'Y',
                 'left', 'right', 'up', 'down',
                 'start', 'select',
                 'LR', 'RR')
        buttons = { x: False for x in names }

        for event in device.read_loop():
            change = False
            if event.type == evdev.ecodes.EV_KEY:
                if event.code in mapping:
                    buttons[mapping[event.code]] = bool(event.value)
                    change = True
            elif event.type == evdev.ecodes.EV_ABS:
                if event.code == 16:
                    buttons['left'] = buttons['right'] = False
                    if event.value < 0:
                        buttons['left'] = True
                    elif event.value > 0:
                        buttons['right'] = True
                    change = True
                elif event.code == 17:
                    buttons['up'] = buttons['down'] = False
                    if event.value < 0:
                        buttons['up'] = True
                    elif event.value > 0:
                        buttons['down'] = True
                    change = True
            if change:
                msg = 0x8000
                for k in buttons.keys():
                    if buttons[k]:
                        msg = msg | bits[k]
                msg ^= 0x7f7f
                b = msg.to_bytes(2)
                print("sending", repr(b))
                await self.lower.write(b)
                await self.lower.flush()


class NESApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "NES/SNES "
    description = """
    Act as a NES or SNES controller
    """

    __pins = ("clock", "latch", "data")

    # need to supply evdev path
    
    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

        for pin in cls.__pins:
            access.add_pin_argument(parser, pin, default=True)

    def build(self, target, args):
        self.mux_interface = iface = \
            target.multiplexer.claim_interface(self, args)
        subtarget = iface.add_subtarget(NESSubtarget(
            out_fifo=iface.get_out_fifo(),
            pads=iface.get_pads(args, pins=self.__pins)
        ))

    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args)
        return NESInterface(iface, self.logger)

    async def interact(self, device, args, nes_iface):
        path = evdev.list_devices()[0]
        await nes_iface.emulate(path)
