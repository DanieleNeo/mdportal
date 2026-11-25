/* MD Portal raw LZMA1 decoder (lc=3, lp=0, pb=2). Original implementation.
   Only accepts the raw stream emitted by portal_compression.py; output length is supplied by the wrapper. */
function mdLzma(input,size){
 let i=0,p=0,range=0xffffffff,code=0,state=0,r0=0,r1=0,r2=0,r3=0;
 const out=new Uint8Array(size), prob=n=>new Uint16Array(n).fill(1024);
 const match=prob(12*4),rep=prob(12),g0=prob(12),g1=prob(12),g2=prob(12),short=prob(12*4),slots=prob(4*64),dist=prob(114),align=prob(16),lit=prob(8*768);
 const len=()=>[prob(2),prob(4*8),prob(4*8),prob(256)],lens=len(),reps=len();
 function byte(){if(i>=input.length)throw Error('LZMA: stream troncato');return input[i++]}
 if(byte()!==0)throw Error('LZMA: intestazione non valida'); for(let j=0;j<4;j++)code=(code*256+byte())>>>0;
 function norm(){if(range<0x1000000){range=(range*256)>>>0;code=(code*256+byte())>>>0}}
 function bit(a,k){const v=a[k],b=(range>>>11)*v;let z;if(code<b){range=b;a[k]=v+((2048-v)>>>5);z=0}else{range=(range-b)>>>0;code=(code-b)>>>0;a[k]=v-(v>>>5);z=1}norm();return z}
 function tree(a,base,n){let x=1;for(let j=0;j<n;j++)x=x*2+bit(a,base+x);return x-(1<<n)}
 function rev(a,base,n){let x=1,v=0;for(let j=0;j<n;j++){const b=bit(a,base+x);x=x*2+b;v+=b*2**j}return v}
 function direct(n){let v=0;for(let j=0;j<n;j++){range>>>=1;let b=0;if(code>=range){code=(code-range)>>>0;b=1}v=v*2+b;norm()}return v}
 function length(a,ps){if(!bit(a[0],0))return tree(a[1],ps*8,3);if(!bit(a[0],1))return 8+tree(a[2],ps*8,3);return 16+tree(a[3],0,8)}
 while(p<size){const ps=p&3,idx=state*4+ps;
  if(!bit(match,idx)){let x=1,base=(p?out[p-1]>>>5:0)*768;
   if(state>=7){if(r0>=p)throw Error('LZMA: distanza non valida');let m=out[p-r0-1];while(x<256){let mb=(m>>>7)&1;m=(m<<1)&255;const b=bit(lit,base+((1+mb)<<8)+x);x=x*2+b;if(b!==mb)break}}
   while(x<256)x=x*2+bit(lit,base+x);out[p++]=x-256;state=state<4?0:state<10?state-3:state-6;continue}
  let n;
  if(bit(rep,state)){
   if(!bit(g0,state)){if(!bit(short,idx)){state=state<7?9:11;n=1}else n=length(reps,ps)+2}
   else{let d;if(!bit(g1,state))d=r1;else{if(!bit(g2,state))d=r2;else{d=r3;r3=r2}r2=r1}r1=r0;r0=d;n=length(reps,ps)+2}
   if(n!==1)state=state<7?8:11;
  }else{r3=r2;r2=r1;r1=r0;n=length(lens,ps)+2;state=state<7?7:10;let s=tree(slots,Math.min(n-2,3)*64,6);if(s<4)r0=s;else{let k=(s>>>1)-1;r0=(2+(s&1))*2**k;if(s<14)r0+=rev(dist,r0-s-1,k);else r0+=direct(k-4)*16+rev(align,0,4)}}
  if(r0>=p||p+n>size)throw Error('LZMA: distanza/lunghezza non valida');while(n--){out[p]=out[p-r0-1];p++}
 }
 return out;
}
