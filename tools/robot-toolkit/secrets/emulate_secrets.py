#!/usr/bin/env python3
"""
Emulate libsecrets.so getters under Unicorn to recover the factory secrets.

libsecrets exposes JNI getters (getDBUsername/Password, getEmbodiedPSK, getEmbodiedStaffPSK,
getFTPUsername/Password). Each copies a ~63-byte obfuscated blob and calls getOriginalKey(buf, len,
packageName, JNIEnv*), which SHA256-derives a key from the package name and decrypts the blob, then
NewStringUTF()s the result. All crypto is internal to the .so, so we run the real code and only mock
libc + a tiny JNIEnv (GetStringUTFChars -> the package name; NewStringUTF -> capture the secret).
"""
import sys, struct, hashlib
from unicorn import *
from unicorn.arm_const import *
from elftools.elf.elffile import ELFFile

SO = sys.argv[1] if len(sys.argv) > 1 else \
     "/home/scubasonar/Code/moxie-robot/work/firmware-re/extract/secrets/lib/armeabi-v7a/libsecrets.so"
PKG = b"me.embodied.productiontesting\x00"

BASE   = 0x0
STACK  = 0x90000000; STACK_SZ = 0x100000
HEAP   = 0x70000000; HEAP_SZ  = 0x100000
STUBS  = 0x40000000  # fake addresses for mocked imports
JNIENV = 0x50000000  # fake JNIEnv table

elf = ELFFile(open(SO, "rb")); data = open(SO, "rb").read()

def align(x, a=0x1000): return (x + a - 1) & ~(a - 1)

class Emu:
    def __init__(self):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_THUMB)
        # enable VFP/NEON (getters use vldr/vstr to move the obfuscated blob)
        try:
            self.uc.reg_write(UC_ARM_REG_C1_C0_2, self.uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xf << 20))
            self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        except Exception as ex:
            import sys; sys.stderr.write("VFP enable warn: %s\n" % ex)
        self.heap = HEAP
        self.captured = None
        # map one contiguous region for the whole image (segments overlap on page granularity)
        top = 0
        for seg in elf.iter_segments():
            if seg['p_type'] == 'PT_LOAD':
                top = max(top, seg['p_vaddr'] + seg['p_memsz'])
        self.uc.mem_map(BASE, align(top) + 0x1000)
        for seg in elf.iter_segments():
            if seg['p_type'] != 'PT_LOAD': continue
            self.uc.mem_write(BASE+seg['p_vaddr'],
                              data[seg['p_offset']:seg['p_offset']+seg['p_filesz']])
        self.uc.mem_map(STACK & ~0xfff, STACK_SZ)
        self.uc.mem_map(HEAP & ~0xfff, HEAP_SZ)
        self.uc.mem_map(STUBS, 0x10000)
        self.uc.mem_map(JNIENV, 0x10000)
        self._relocate()
        self._plt()
        self._jnienv()
        self.uc.hook_add(UC_HOOK_CODE, self._on_code)
        # capture the obfuscated blob + length at getOriginalKey() entry (0x1014).
        self.blob=None
        def _gok(uc, address, size, u):
            b=uc.reg_read(UC_ARM_REG_R0); ln=uc.reg_read(UC_ARM_REG_R1)
            if 0 < ln < 512:
                self.blob=bytes(uc.mem_read(b, ln))
        self.uc.hook_add(UC_HOOK_CODE, _gok, begin=0x1014, end=0x1015)

    def malloc(self, b):
        a = self.heap; self.uc.mem_write(a, b); self.heap += align(len(b), 16); return a

    def _relocate(self):
        rd = elf.get_section_by_name('.rel.dyn')
        if not rd: return
        for r in rd.iter_relocations():
            if r['r_info_type'] == 23:  # R_ARM_RELATIVE: *off += BASE
                off = r['r_offset']
                cur = struct.unpack("<I", self.uc.mem_read(BASE+off, 4))[0]
                self.uc.mem_write(BASE+off, struct.pack("<I", cur + BASE))

    def _plt(self):
        # point each JUMP_SLOT GOT entry at a unique stub address we hook
        sym = elf.get_section_by_name('.dynsym')
        rp = elf.get_section_by_name('.rel.plt')
        self.stub_names = {}
        i = 0
        for r in rp.iter_relocations():
            symn = sym.get_symbol(r['r_info_sym'])
            nm = symn.name
            if symn['st_value'] != 0 and symn['st_shndx'] != 'SHN_UNDEF':
                # internally defined (crypto): resolve to real addr, Thumb bit set
                self.uc.mem_write(BASE+r['r_offset'], struct.pack("<I", (BASE+symn['st_value'])|1))
            else:
                stub = STUBS + i*4; i += 1
                self.uc.mem_write(BASE+r['r_offset'], struct.pack("<I", stub))
                self.stub_names[stub] = nm

    def _jnienv(self):
        # JNIEnv* -> table pointer; fill NewStringUTF(167), GetStringUTFChars(169),
        # GetStringUTFLength(168), ReleaseStringUTFChars(170) with stub addrs.
        self.env_ptr = JNIENV
        table = JNIENV + 0x1000
        self.uc.mem_write(JNIENV, struct.pack("<I", table))
        self.jni_stub = {}
        for idx, nm in [(167,'NewStringUTF'),(168,'GetStringUTFLength'),
                        (169,'GetStringUTFChars'),(170,'ReleaseStringUTFChars')]:
            s = STUBS + 0x8000 + idx*4
            self.uc.mem_write(table + idx*4, struct.pack("<I", s))
            self.jni_stub[s] = nm

    def _ret(self, uc, r0=0):
        uc.reg_write(UC_ARM_REG_R0, r0 & 0xffffffff)
        lr = uc.reg_read(UC_ARM_REG_LR)
        uc.reg_write(UC_ARM_REG_PC, lr & ~1)
        # keep thumb bit per lr
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        uc.reg_write(UC_ARM_REG_CPSR, (cpsr | 0x20) if (lr & 1) else (cpsr & ~0x20))

    def _on_code(self, uc, addr, size, u):
        a = addr | (0 if not (uc.reg_read(UC_ARM_REG_CPSR)&0x20) else 0)
        # PLT libc stubs
        if addr in self.stub_names:
            self._libc(uc, self.stub_names[addr]); return
        if addr in self.jni_stub:
            self._jni(uc, self.jni_stub[addr]); return

    def _rd(self, uc, ptr, n): return bytes(uc.mem_read(ptr, n))
    def _cstr(self, uc, ptr):
        out=b""; 
        while True:
            c=uc.mem_read(ptr+len(out),1)
            if c==b"\x00": break
            out+=bytes(c)
            if len(out)>256: break
        return out

    def _libc(self, uc, nm):
        R=lambda i: uc.reg_read(getattr(uc, f"reg_read") and [UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3][i])
        r0=uc.reg_read(UC_ARM_REG_R0); r1=uc.reg_read(UC_ARM_REG_R1); r2=uc.reg_read(UC_ARM_REG_R2)
        if nm in ('__aeabi_memcpy','memcpy','__aeabi_memcpy4','__aeabi_memcpy8'):
            uc.mem_write(r0, self._rd(uc,r1,r2)); self._ret(uc, r0)
        elif nm in ('__aeabi_memclr','__aeabi_memclr4','__aeabi_memclr8'):
            uc.mem_write(r0, b"\x00"*r1); self._ret(uc, r0)
        elif nm=='memset':
            uc.mem_write(r0, bytes([r1&0xff])*r2); self._ret(uc, r0)
        elif nm=='strlen':
            self._ret(uc, len(self._cstr(uc,r0)))
        elif nm in ('sprintf','snprintf'):
            self._sprintf(uc, nm); return
        elif nm=='__aeabi_uidivmod':
            q,rem=(r0//r1, r0%r1) if r1 else (0,0)
            uc.reg_write(UC_ARM_REG_R0,q&0xffffffff); uc.reg_write(UC_ARM_REG_R1,rem&0xffffffff)
            lr=uc.reg_read(UC_ARM_REG_LR); uc.reg_write(UC_ARM_REG_PC, lr&~1)
        elif nm in ('__stack_chk_fail','abort','raise','__aeabi_idiv0'):
            uc.emu_stop()
        else:
            self._ret(uc, 0)  # __cxa_*, dladdr, fprintf, fflush, unwind: no-op

    def _jni(self, uc, nm):
        r1=uc.reg_read(UC_ARM_REG_R1); r2=uc.reg_read(UC_ARM_REG_R2)
        if nm=='GetStringUTFChars':
            # (env, jstring, isCopy) -> char*  ; our jstring buffer holds the pkg name
            self._ret(uc, self.pkg_ptr)
        elif nm=='GetStringUTFLength':
            self._ret(uc, len(PKG)-1)
        elif nm=='NewStringUTF':
            # (env, char*) -> jstring ; r1 = the decrypted secret!
            self.captured = self._cstr(uc, r1)
            self._ret(uc, 0x1234)  # fake jstring
        elif nm=='ReleaseStringUTFChars':
            self._ret(uc, 0)
        else:
            self._ret(uc, 0)

    def _sprintf(self, uc, nm):
        import struct as _st
        if nm=='snprintf':
            dst=uc.reg_read(UC_ARM_REG_R0); size=uc.reg_read(UC_ARM_REG_R1)
            fmt=uc.reg_read(UC_ARM_REG_R2); argregs=[UC_ARM_REG_R3]; stack_start=0
        else:
            dst=uc.reg_read(UC_ARM_REG_R0); size=1<<30
            fmt=uc.reg_read(UC_ARM_REG_R1); argregs=[UC_ARM_REG_R2, UC_ARM_REG_R3]; stack_start=0
        fmts=self._cstr(uc, fmt)
        # gather integer/pointer args: from remaining core regs then the stack
        args=[]
        for r in argregs: args.append(uc.reg_read(r))
        sp=uc.reg_read(UC_ARM_REG_SP)
        stackwords=[_st.unpack('<I', bytes(uc.mem_read(sp+4*i,4)))[0] for i in range(16)]
        argpool=args+stackwords
        ai=0
        out=b""; i=0
        while i < len(fmts):
            c=fmts[i:i+1]
            if c==b'%':
                j=i+1
                spec=b''
                while j < len(fmts) and fmts[j:j+1] not in b'diouxXscp%':
                    spec+=fmts[j:j+1]; j+=1
                conv=fmts[j:j+1]; 
                if conv==b'%': out+=b'%'; i=j+1; continue
                val=argpool[ai]; ai+=1
                if conv in (b'x',b'X'):
                    hexs=('%'+spec.decode()+conv.decode())%val
                    out+=hexs.encode()
                elif conv in (b'd',b'i',b'u'):
                    out+=('%'+spec.decode()+'d'%0 if False else ('%'+spec.decode()+'d')%val).encode()
                elif conv==b'c': out+=bytes([val&0xff])
                elif conv in (b's',b'p'):
                    out+=self._cstr(uc, val)
                i=j+1
            else:
                out+=c; i+=1
        out=out[:max(0,size-1)]
        uc.mem_write(dst, out+b"\x00")
        self._ret(uc, len(out))

    def call_getter(self, vaddr):
        uc=self.uc
        self.captured=None
        self.pkg_ptr=self.malloc(PKG)
        jstr=self.malloc(struct.pack("<I", self.pkg_ptr))  # fake jstring object
        sp=STACK+STACK_SZ-0x400
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_R0, self.env_ptr)   # JNIEnv*
        uc.reg_write(UC_ARM_REG_R1, 0)              # jobject thiz
        uc.reg_write(UC_ARM_REG_R2, jstr)           # jstring packageName
        ret=0xdead0000
        uc.reg_write(UC_ARM_REG_LR, ret|1)
        try:
            uc.emu_start(vaddr|1, ret, timeout=5_000_000, count=2_000_000)
        except UcError as e:
            pass
        return self.captured

sym=elf.get_section_by_name('.dynsym')
getters={s.name.split('_get')[-1]:s['st_value'] for s in sym.iter_symbols()
         if s.name.startswith('Java_me_embodied_productiontesting_Secrets_get')}
# The real getOriginalKey XORs the blob with ASCII(hex(sha256(packageName))). We emulate the
# getter only to *capture the assembled blob* (the lib's own SHA256 miscomputes under Unicorn), then
# derive the key correctly in Python -> clean plaintext.
KEY = hashlib.sha256(PKG[:-1]).hexdigest().encode()
def decrypt(blob):
    return bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(blob))

emu=Emu()
print("secret,value")
for name,va in sorted(getters.items()):
    emu.call_getter(va)
    val = decrypt(emu.blob) if emu.blob else b""
    print(f"{name},{val.decode(errors='replace') if val else '<none>'}")
