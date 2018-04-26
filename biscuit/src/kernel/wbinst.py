#!/usr/bin/env python2
# vim: expandtab ts=4 sw=4

import piper

from capstone import *
from capstone.x86 import *

def opdump(x, op, c):
    if op.type == X86_OP_REG:
        print("\t\toperands[%u].type: REG = %s" % (c, x.reg_name(op.reg)))
    if op.type == X86_OP_IMM:
        print("\t\toperands[%u].type: IMM = 0x%s" % (c, op.imm))
    if op.type == X86_OP_FP:
        print("\t\toperands[%u].type: FP = %f" % (c, op.fp))
    if op.type == X86_OP_MEM:
        print("\t\toperands[%u].type: MEM" % c)
        if op.mem.segment != 0:
            print("\t\t\toperands[%u].mem.segment: REG = %s" % (c, x.reg_name(op.mem.segment)))
        if op.mem.base != 0:
            print("\t\t\toperands[%u].mem.base: REG = %s" % (c, x.reg_name(op.mem.base)))
        if op.mem.index != 0:
            print("\t\t\toperands[%u].mem.index: REG = %s" % (c, x.reg_name(op.mem.index)))
        if op.mem.scale != 1:
            print("\t\t\toperands[%u].mem.scale: %u" % (c, op.mem.scale))
        if op.mem.disp != 0:
            print("\t\t\toperands[%u].mem.disp: 0x%s" % (c, op.mem.disp))

class Sym(object):
    def __init__(self, name, s, e):
        self.name, self.start, self.end = name, s, e

    # returns true if v lies within the symbol's addresses
    def within(self, v):
        return not (v < self.start or v >= self.end)

def symlookup(fn, sym):
    c1 = ['nm', '-C', fn]
    c2 = ['sort']
    c3 = ['grep', '-A10', '-w', sym]
    out, _ = piper.piper([c1, c2, c3])
    lines = filter(None, [l.strip() for l in out.split('\n')])
    start = int(lines[0].split()[0], 16)
    end = 0
    found = False
    #for x in lines:
    #    print x
    for l in lines[1:]:
        end = int(l.split()[0], 16)
        if end > start:
            found = True
            break
    if not found:
        raise 'no end?'
    return Sym(sym, start, end)

# returns a list of output lines
def readelfgrep(fn, rf, gre):
    c1 = ['readelf'] + rf + [fn]
    c2 = ['grep'] + gre
    out, _ = piper.piper([c1, c2])
    out = filter(None, out.split('\n'))
    return out

class Basicblock(object):
    def __init__(self, firstaddr, addrs, succs):
        self.firstaddr, self.addrs, self.succs = firstaddr, addrs, succs
        self.preds = {}
        if self.firstaddr != self.addrs[0]:
            raise 'no'

class Params(object):
    def __init__(self, fn):
        self._syms = {}
        self._initsym(fn, 'writeBarrier')
        self._initsym(fn, 'type\.\*')
        self._initsym(fn, 'panicindex')

        d = readelfgrep(fn, ['-S'], ['\.text.*PROGBIT'])[0].split()
        foff = int(d[5], 16)
        textva = int(d[4], 16)

        d = readelfgrep(fn, ['-l'], ['-E', '[[:digit:]]{2}.*\.text\>'])
        textseg = int(d[0].split()[0])

        d = readelfgrep(fn, ['-S'], ['-A1', '\.text.*PROGBIT'])[1].split()
        textsz = int(d[0], 16)
        self._endva = textva + textsz

        print '.text file offset:', hex(foff)
        print '.text endva:', hex(self._endva)
        print '.text VA:', hex(textva), ('(seg %d)' % (textseg))

        with open('main.gobin', 'rb') as f:
            d = f.read()
        d = d[foff:]
        d = d[:textsz]
        data = d
        #data = data[:3000]

        md = Cs(CS_ARCH_X86, CS_MODE_64)
        md.detail = True
        md.syntax = CS_OPT_SYNTAX_ATT

        ilist = [x for x in md.disasm(data, textva)]
        ilist = sorted(ilist, key=lambda x: x.address)
        #ilist = filter(None, [x if x.address < self._endva else None for x in ilist])
        for i, x in enumerate(ilist):
                x.idx = i
        self._ilist = ilist

        iaddr = {}
        for x in self._ilist:
                iaddr[x.address] = x
        self._iaddr = iaddr

        self._jmps = [ X86_INS_JAE, X86_INS_JA, X86_INS_JBE, X86_INS_JB,
        X86_INS_JCXZ, X86_INS_JECXZ, X86_INS_JE, X86_INS_JGE, X86_INS_JG,
        X86_INS_JLE, X86_INS_JL, X86_INS_JMP, X86_INS_JNE, X86_INS_JNO,
        X86_INS_JNP, X86_INS_JNS, X86_INS_JO, X86_INS_JP, X86_INS_JRCXZ,
        X86_INS_JS ]

    def _initsym(self, fn, sym):
        s = symlookup(fn, sym)
        self._syms[sym] = s
        print 'SYM %s %#x %#x' % (s.name, s.start, s.end)

    # returns true if ins is the first instruction of a write barrier check
    def iswb(self, ins):
        if ins.id != X86_INS_MOV:
            return False
        if len(ins.operands) != 2:
            return False
        # operand indicies match chosen syntax, which is intel by default
        src, dst = ins.operands[0], ins.operands[1]
        if dst.type == X86_OP_REG and src.type == X86_OP_MEM:
            if src.mem.base != X86_REG_RIP:
                return False
            addr = src.value.mem.disp + ins.address + ins.size
            wbsym = p._syms['writeBarrier']
            if wbsym.within(addr):
                return True
        return False

    # returns true if ins is the first instruction of a type assertion or
    # switch
    def istc(self, ins):
        if ins.id != X86_INS_LEA:
            return False
        if len(ins.operands) != 2:
            return False
        src, dst = ins.operands[0], ins.operands[1]
        if dst.type != X86_OP_REG or src.type != X86_OP_MEM:
            return False
        if src.mem.base != X86_REG_RIP:
            return False
        addr = src.value.mem.disp + ins.address + ins.size
        typesym = p._syms['type\.\*']
        if not typesym.within(addr):
            return False
        reg = self.regops(ins, 1)[0]
        n = self.next(ins)
        if n.id not in [X86_INS_CMP] or not self.uses(n, reg):
            return False
        return True

    # return true if x has an operand that uses register reg
    def uses(self, x, reg):
        n = x.op_count(X86_OP_REG)
        for i in range(n):
            op = x.op_find(X86_OP_REG, i + 1)
            if op.reg == reg:
                return True
        return False

    # returns the register IDs used by instruction x
    def regops(self, ins, exp):
        ret = [x.reg for x in filter(lambda op: op.type == X86_OP_REG, ins.operands)]
        if len(ret) != exp:
            raise 'mismatch expect'
        return ret

    def next(self, x):
        if x.idx + 1 >= len(self._ilist):
            return None
        return self._ilist[x.idx + 1]

    # returns the first instruction after ins which uses register reg for an
    # operand
    def findnextreg(self, ins, reg, bound):
        i = 0
        while bound == -1 or i < bound:
            i += 1
            ins = self.next(ins)
            if self.uses(ins, reg):
                return ins
        raise 'didnt find within bound'

    def findnext(self, ins, xids, bound):
        i = 0
        pr = ins.address == 0x54e491
        while bound == -1 or i < bound:
            #if pr:
            #    print 'AT', ins.mnemonic, ins.op_str
            i += 1
            ins = self.next(ins)
            if ins.id in xids:
                return ins
        print 'ADDR', hex(ins.address)
        raise 'didnt find within bound'

    # returns the first jump instructions after ins
    def findnextjmp(self, ins, bound):
        return self.findnext(ins, self._jmps, bound)

    # returns the first call instructions after ins
    def findnextcall(self, ins, bound):
        return self.findnext(ins, [X86_INS_CALL], bound)

    def ensure(self, ins, xids):
        if ins.id not in xids:
            print '%d != %s (%s %s)' % (ins.id, xids, ins.mnemonic, ins.op_str)
            raise 'mismatch'

    def writebarrierins(self):
        '''
        finds write barrier checks of the form:
        mov     writebarrierflag, REG
        test    REG
        jnz     1
        2:
            ...
        1:
            ...
            call    writebarrierfunc
            ...
            jmpq    2

        and returns them as a list. all the instructions and "..."s above
        except for the "..." between the 2 and 1 labels are included in the
        returned set
        '''
        wb = []
        for x in p._ilist:
            if p.iswb(x):
                wb.append(x)
                reg = p.regops(x, 1)
                reg = reg[0]
                # find next instruction which uses the register into which the flag was
                # loaded. it should be a test.
                n = p.findnextreg(x, reg, 20)
                p.ensure(n, [X86_INS_TEST])
                wb.append(n)
                n = p.findnextjmp(n, 20)
                p.ensure(n, [X86_INS_JNE])
                wb.append(n)
                if len(n.operands) != 1:
                    raise 'no'
                # add the block executed when write barrier is enabled
                addr = n.operands[0].imm
                n = p._iaddr[addr]

                call = p.findnextcall(n, 10)
                jmp = p.findnextjmp(n, 20)
                p.ensure(jmp, [X86_INS_JMP])
                # make sure the jump comes after the call
                if jmp.address - call.address < 0:
                    raise 'call must come first'

                while n.address <= jmp.address:
                    wb.append(n)
                    n = p.next(n)
        return wb

    def typechecks(self):
        raise 'broken'
        ret = []
        for x in p._ilist:
            if not p.istc(x):
                continue
            ret.append(x.address)
        return ret

    def isnilchk(self, ins):
        '''
        finds all nil pointer checks of the form:
            mov     ptr, REG
            test    %al, (REG)

        at least go1.8 and go1.10.1 always uses %al for nil pointer checks
        '''
        if ins.id != X86_INS_TEST:
            return False
        if len(ins.operands) != 2:
            return False
        # operand indicies match chosen syntax, which is intel by default
        al, mem = ins.operands[0], ins.operands[1]
        if al.type != X86_OP_REG or mem.type != X86_OP_MEM:
            return False
        if al.reg != X86_REG_AL:
            return False
        if mem.mem.base == 0 or mem.mem.disp != 0 or mem.mem.index != 0 or mem.mem.scale != 1:
            return False
        return True

    def isimmcmp(self, ins):
        return ins.id == X86_INS_CMP and ins.operands[0].type == X86_OP_IMM

    def ptrchecks(self):
        ret = []
        for x in p._ilist:
            if p.isnilchk(x):
                ret.append(x.address)
        return ret

    def prbb(self, bb):
        print '------- %x -----' % (bb.firstaddr)
        print 'PREDS', ' '.join(['%x' % (x) for x in bb.preds])
        for caddr in bb.addrs:
            ins = self._iaddr[caddr]
            print '%x: %s %s' % (ins.address, ins.mnemonic, ins.op_str)
        print 'SUCS', ' '.join(['%x' % (x) for x in bb.succs])
        #print '--------------------'

    # returns map of first instruction of basic block to basic block
    def bbs(self):
        in2b = {}
        # map of first instruction of basic block to basic block
        bb = {}
        # the go compiler uses ud2 and int3 for padding instructions that
        # shouldn't be reached; use them as a basic block boundary too.
        bstops = [X86_INS_UD2, X86_INS_INT3]
        bends = self._jmps + [X86_INS_RET] + bstops
        caddr = self._ilist[0].address
        while caddr in p._iaddr:
            #print 'VISIT', hex(caddr)
            ins = p._iaddr[caddr]
            end = self.findnext(ins, bends, -1)
            # only a few special runtime functions have jumps that are not
            # immediates and can be safely ignored since their destinations
            # will be visited anyway.
            sucs = []
            if end.id == X86_INS_JMP:
                # block has one successor
                if end.operands[0].type == X86_OP_IMM:
                    sucs = [end.operands[0].imm]
                else:
                    # ignore special reg dest
                    sucs = []
            elif end.id == X86_INS_RET:
                # block has no successors
                sucs = []
            elif end.id in bstops:
                # this instruction shouldn't be reached, but it may be helpful
                # to know what code follows
                sucs = [end.address + end.size]
            else:
                # must be conditional jump; block has two successors
                conds = list(set(p._jmps).difference(set([X86_INS_JMP])))
                p.ensure(end, conds)
                sucs = [end.operands[0].imm]
                tmp = end.address + end.size
                # avoid duplicate successors if the conditional branch target
                # is also the following instruction
                if tmp in p._iaddr and tmp != sucs[0]:
                    sucs.append(tmp)
            baddrs = []
            while ins is not None and ins.address <= end.address:
                baddrs.append(ins.address)
                ins = p.next(ins)
            newb = Basicblock(baddrs[0], baddrs, sucs)
            bb[newb.firstaddr] = newb
            for addr in baddrs:
                in2b[addr] = newb

            caddr = end.address + end.size

        for b in bb.values():
            if b.firstaddr not in bb:
                raise 'wtf'

        # split blocks
        changed = True
        while changed:
            changed = False
            addblocks = {}
            for b in bb.values():
                for s in b.succs:
                    if s in bb or s in addblocks:
                        continue
                    # the in2b map ensures that we lookup the most recently
                    # split block for every jump into the middle of a block
                    oldb = in2b[s]
                    for oa in oldb.addrs:
                        del in2b[oa]
                    old = filter(None, [x if x < s else None for x in oldb.addrs])
                    new = filter(None, [x if x >= s else None for x in oldb.addrs])
                    newfirst = new[0]
                    oldb.addrs = old
                    newsuccs = oldb.succs
                    oldb.succs = [newfirst]
                    newb = Basicblock(newfirst, new, newsuccs)
                    if newfirst in addblocks:
                        raise 'no'
                    addblocks[newfirst] = newb
                    for oa in old:
                        in2b[oa] = oldb
                    for oa in new:
                        in2b[oa] = newb
                    #print 'SPLIT %x -> %x %x' % (s, oldb.firstaddr, newb.firstaddr)

            for ab in addblocks:
                changed = True
                if ab in bb:
                    raise 'double add'
                bb[ab] = addblocks[ab]

        # pass two creates predecessor lists
        for b in bb.values():
            for s in b.succs:
                if s not in bb:
                    print 'NOT FOUND %x' % (s)
                    if s not in addblocks:
                        print 'NO ADD %x' % (s)
                    durb = in2b[s]
                    p.prbb(durb)
                    raise 'crud'
                tb = bb[s]
                tb.preds[b.firstaddr] = True
        # sanity
        for b in bb.values():
            if len(b.succs) > 2:
                raise 'no'
            if len(b.succs) == 2 and b.succs[0] == b.succs[1]:
                p.prbb(b)
                raise 'no'
        self._bb = bb
        return bb

    # returns list of instructions for basic block beginning at address baddr
    def bbins(self, baddr):
        return [self._iaddr[x] for x in p._bb[baddr].addrs]

    def isboundblk(self, baddr):
        for ins in p.bbins(bb):
            if ins.id == X86_INS_CALL and ins.operands[0].type == X86_OP_IMM:
                if panicsym.within(ins.operands[0].imm):
                    return True
        return False

p = Params('main.gobin')
print 'made all map: %d' % (len(p._ilist))

#found = p.typechecks()
#found = p.ptrchecks()
#found = p.boundschecks()
found = p.bbs()

print 'found', len(found), 'blocks'
panicsym = p._syms['panicindex']
for baddr in sorted(found):
    #p.prbb(baddr)
    if p.isboundblk(baddr):
            #p.prbb(found[baddr])
            paddrs = [x for x in found[baddr].preds]

#wb = p.writebarrierins()
#
#with open('wb.txt', 'w') as f:
#    for w in wb:
#        print >> f, '%x' % (w.address)

#print 'wb list:', len(wb)
#mp = {}
#for w in wb:
#    mp[w.address] = True
#print 'wb map:', len(mp)

