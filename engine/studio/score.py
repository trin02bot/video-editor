"""Original trailer score, generated procedurally (no licensing): low drone bed, risers with a split
second of silence before each big moment, BRAAM + impact hits from the second act on, staccato strings
and war drums that grow toward the end, a snare roll and a final hit on the title card.
Ported from the Episode 3 trailer score that the client approved."""
from __future__ import annotations

import numpy as np
import scipy.signal as ss
import scipy.io.wavfile as wavfile

SR = 48000


def trailer_score(st: list[float], out_path, title_seconds: float = 3.2, seed: int = 3) -> str:
    """st: bite start times (seconds) followed by the time the title card starts."""
    global RNG
    RNG = np.random.default_rng(seed)
    n_b = len(st) - 1
    i_act2 = max(1, round(n_b * 0.45)); i_lift = max(i_act2 + 1, round(n_b * 0.78))
    VEND = st[-1]; ACT2 = st[i_act2]; LIFT = st[min(i_lift, n_b - 1)]; DUR = VEND + title_seconds
    N = int(DUR * SR); t = np.arange(N) / SR
    mtof=lambda m:440*2**((m-69)/12)
    def lp(x,fc,o=2): return ss.sosfilt(ss.butter(o,fc,'lp',fs=SR,output='sos'),x)
    def hp(x,fc,o=2): return ss.sosfilt(ss.butter(o,fc,'hp',fs=SR,output='sos'),x)
    def bp(x,a,b): return ss.sosfilt(ss.butter(2,[a,b],'bp',fs=SR,output='sos'),x)
    def saw(f,n,kmax=30):
        tt=np.arange(n)/SR; x=np.zeros(n)
        for k in range(1,kmax+1):
            if f*k>14000: break
            x+=np.sin(2*np.pi*f*k*tt+k*1.3)/k
        return x
    def add(buf,x,t0,g=1.0):
        i0=int(round(t0*SR)); a=max(0,-i0); i0=max(0,i0); i1=min(len(buf),i0+len(x)-a)
        if i1>i0: buf[i0:i1]+=x[a:a+i1-i0]*g
    L=int(3.5*SR); ir=RNG.standard_normal(L)*np.exp(-np.arange(L)/SR*1.6); ir=lp(ir,5000); ir/=np.sqrt((ir**2).sum())
    def verb(x,mix): return x*(1-mix)+ss.fftconvolve(x,ir)[:len(x)]*mix
    # ---------- sound design elements
    def impact(big=1.0):
        n=int(3.0*SR); tt=np.arange(n)/SR
        sub=np.sin(2*np.pi*np.cumsum(32+90*np.exp(-tt*9))/SR)*np.exp(-tt*1.4)
        body=lp(RNG.standard_normal(n),700)*np.exp(-tt*9)*0.9
        crack=hp(RNG.standard_normal(n),2500)*np.exp(-tt*40)*0.5
        tom=np.sin(2*np.pi*np.cumsum(70+60*np.exp(-tt*20))/SR)*np.exp(-tt*5)*0.7
        return np.tanh((sub*1.2+body+crack+tom)*1.4)*big
    def whoosh(d=0.55):
        n=int(d*SR); tt=np.arange(n)/SR; nz=RNG.standard_normal(n); o=np.zeros(n); fs=np.geomspace(300,6000,n)
        for j in range(0,n,1200): o[j:j+1200]=bp(nz[j:j+1200],fs[j]*0.7,min(fs[j]*1.4,20000))
        return o*(tt/d)**2.5*0.7
    def braam(root=38,d=3.2):
        n=int(d*SR); tt=np.arange(n)/SR; x=np.zeros(n)
        for m,g in ((root,1.0),(root+12,0.8),(root+19,0.45),(root+24,0.35)):
            for det in (-0.15,-0.05,0.05,0.15): x+=saw(mtof(m+det),n)*g
        env=np.minimum(1,tt/0.06)*np.exp(-tt*0.9)
        out=np.zeros(n); blk=2400
        for j in range(0,n,blk):
            fc=300+2600*np.exp(-(j/SR)*1.8)*min(1,(j/SR)/0.08+0.2); out[j:j+blk]=lp(x[max(0,j-2048):j+blk],fc)[-len(x[j:j+blk]):]
        out=np.tanh(out*0.35)*env
        out+=np.sin(2*np.pi*mtof(root-12)*tt)*env*0.9
        return out*0.9
    def taiko(g=1.0):
        n=int(0.8*SR); tt=np.arange(n)/SR
        x=np.sin(2*np.pi*np.cumsum(60+80*np.exp(-tt*25))/SR)*np.exp(-tt*6)+lp(RNG.standard_normal(n),500)*np.exp(-tt*18)*0.6
        return x*g
    def tick(g=1.0):
        n=int(0.05*SR); tt=np.arange(n)/SR; return (bp(RNG.standard_normal(n),2500,9000)*0.6+np.sin(2*np.pi*3200*tt)*0.3)*np.exp(-tt*90)*g
    drone=np.zeros(N); perc=np.zeros(N); strings=np.zeros(N); sfx=np.zeros(N); ticks=np.zeros(N); rise=np.zeros(N)
    SUCK=0.22
    # ---------- drone bed with crescendo + opening filter across whole trailer
    n=int(VEND*SR); tt=np.arange(n)/SR; d=np.zeros(n)
    for m,g in ((26,1.0),(38,0.6),(45,0.3),(50,0.15)):
        for det in (-0.08,0.08): d+=saw(mtof(m+det),n,16)*g
    out=np.zeros(n); blk=4800
    for j in range(0,n,blk):
        fc=320; out[j:j+blk]=lp(d[max(0,j-4096):j+blk],fc)[-len(d[j:j+blk]):]
    d=out*0.8+lp(RNG.standard_normal(n),200)*0.2
    add(drone,d*np.minimum(1,tt/1.5),0,0.10)
    # ---------- risers into each big moment (+ suck-out silence)
    def riser(t_end,d,g):
        i0,i1=int((t_end-d)*SR),int(t_end*SR); nz=RNG.standard_normal(i1-i0); tt=np.arange(i1-i0)/SR; o=np.zeros_like(nz); fs=np.geomspace(200,9000,len(nz))
        for j in range(0,len(nz),2400): o[j:j+2400]=bp(nz[j:j+2400],fs[j]*0.7,min(fs[j]*1.4,20000))
        f=np.geomspace(110,880,i1-i0)
        sfx[i0:i1]+=(o*0.7+np.sin(2*np.pi*np.cumsum(f)/SR)*0.18)*(tt/tt[-1])**2.3*g
    gate=np.ones(N)
    for T0,dd,g in ((ACT2,3.2,0.8),(LIFT,3.0,0.9),(VEND,4.0,1.0)):
        riser(T0-SUCK,dd,g)
        i0=int((T0-SUCK)*SR); i1=int(T0*SR); gate[i0:i1]=0.0
    gate[int(VEND*SR):]=0.0
    # ---------- endless rising layer (shepard-style) act2 -> title
    i0,i1=int(ACT2*SR),int((VEND-SUCK)*SR); tt=np.arange(i1-i0)/SR; T=tt[-1]; x=np.zeros(i1-i0)
    for o in range(4):
        ph=(tt/6.0+o/4)%1.0; f=55*2**(ph*4); amp=np.sin(np.pi*ph)**2
        x+=np.sin(2*np.pi*np.cumsum(f)/SR)*amp
    rise[i0:i1]+=lp(x,2500)*(0.3+0.7*(tt/T))*0.06
    # ---------- act2/lift: staccato strings (filter opens, gets denser) + war drums building
    B=0.5; s16=B/4; PAT=[0,0,0,3,0,0,5,0, 0,0,0,3,0,7,5,3]
    tc=ACT2; k=0
    while tc<VEND-SUCK-0.02:
        lift=tc>=LIFT-0.01
        if (not lift) and tc<ACT2+4*B and k%2==1: tc+=s16; k+=1; continue      # starts in 8ths, then 16ths
        m=38+PAT[k%16]+(12 if lift else 0); n=int(s16*0.8*SR)
        x=(saw(mtof(m),n,20)+saw(mtof(m+0.1),n,20)+0.6*saw(mtof(m+12),n,14))*np.exp(-np.arange(n)/SR*18)
        prog=(tc-ACT2)/(VEND-ACT2)
        add(strings,x,tc,(0.08+0.07*prog)*(1.0 if k%4==0 else 0.7)); tc+=s16; k+=1
    out=np.zeros(N); blk=4800
    for j in range(int(ACT2*SR)-blk,N,blk):
        tcj=j/SR; fc=1400+3400*min(1,max(0,(tcj-ACT2)/(VEND-ACT2))); out[j:j+blk]=lp(strings[max(0,j-4096):j+blk],fc)[-len(strings[j:j+blk]):]
    strings=out
    tb=ACT2; q=0
    while tb<VEND-SUCK-0.05:
        lift=tb>=LIFT-0.01; prog=(tb-ACT2)/(VEND-ACT2)
        add(perc,taiko((0.9 if q%4==0 else 0.55)*(0.75+0.4*prog)),tb)
        if lift or q%2==1: add(perc,taiko(0.45),tb+B/2)
        if lift: add(perc,taiko(0.35),tb+B*0.75)
        tb+=B; q+=1
    # ---------- accelerating snare roll into the title
    def snare(g):
        n=int(0.15*SR); tt=np.arange(n)/SR; return (bp(RNG.standard_normal(n),1500,7000)*0.8+np.sin(2*np.pi*200*tt)*0.4)*np.exp(-tt*26)*g
    tr=VEND-SUCK-3.0; iv=0.25
    while tr<VEND-SUCK-0.03:
        frac=1-(VEND-SUCK-tr)/3.0; add(perc,snare(0.15+0.45*frac**1.5),tr); iv=max(0.045,0.25*(1-frac)**1.6); tr+=iv
    # ---------- hits on every cut (big ones get BRAAM)
    for i in range(i_act2,len(st)-1):
        tcut=st[i]; big=i in (i_act2,i_lift)
        add(sfx,whoosh(0.5),tcut-0.5-(SUCK if big else 0),0.8)
        add(sfx,impact(1.2 if big else 0.7),tcut)
        if big: add(sfx,braam(38 if i==i_act2 else 41),tcut,1.1)
    final=np.zeros(N); add(final,impact(1.5),VEND); add(final,braam(38,3.2),VEND,1.3)
    ramp=np.ones(N)
    for a_,b_ in ((0,ACT2),(ACT2,LIFT),(LIFT,VEND)):
        i0,i1=int(a_*SR),int(b_*SR); ramp[i0:i1]=1.0
    mix=((verb(drone,0.2)+verb(perc,0.25)+verb(strings,0.25)+verb(rise,0.3))*ramp)*gate+verb(sfx,0.3)*np.where(gate>0,1,0.0)+verb(final,0.3)
    mix=hp(mix,28); mix[-int(0.8*SR):]*=np.linspace(1,0,int(0.8*SR))
    mix = hp(mix, 28); mix[-int(0.8 * SR):] *= np.linspace(1, 0, int(0.8 * SR))
    mix /= np.max(np.abs(mix)) * 1.08
    wavfile.write(str(out_path), SR, np.stack([mix, mix], 1).astype(np.float32))
    return str(out_path)
