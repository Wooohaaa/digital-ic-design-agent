/* IC Forge TinyStories260K inference. Algorithm: karpathy/llama2.c (MIT).
 * This implementation keeps the checkpoint FP32 and caps the KV context at 64.
 * No model computation is performed by the UART host. */
#include <stdint.h>
#include <math.h>
#define DIM 64
#define HIDDEN 172
#define LAYERS 5
#define HEADS 8
#define KV 32
#define VOCAB 512
#define CONTEXT 64
static const float *emb, *ra, *wq, *wk, *wv, *wo, *rf, *w1, *w2, *w3, *rfinal;
static float x[DIM], xb[DIM], xb2[DIM], hb[HIDDEN], hb2[HIDDEN], q[DIM];
static float keys[LAYERS][CONTEXT][KV], values[LAYERS][CONTEXT][KV];
static float att[CONTEXT], logits[VOCAB];
#include "rope.h"

int ts_init(const void *image) {
    const int32_t *h = (const int32_t *)image;
    if (h[0]!=DIM || h[1]!=HIDDEN || h[2]!=LAYERS || h[3]!=HEADS ||
        h[4]!=4 || h[5]!=VOCAB || h[6]!=512) return -1;
    const float *p = (const float *)(h+7);
    emb=p; p+=VOCAB*DIM; ra=p; p+=LAYERS*DIM;
    wq=p; p+=LAYERS*DIM*DIM; wk=p; p+=LAYERS*DIM*KV;
    wv=p; p+=LAYERS*DIM*KV; wo=p; p+=LAYERS*DIM*DIM;
    rf=p; p+=LAYERS*DIM; w1=p; p+=LAYERS*DIM*HIDDEN;
    w2=p; p+=LAYERS*HIDDEN*DIM; w3=p; p+=LAYERS*DIM*HIDDEN;
    rfinal=p;
    return 0;
}
static void mm(float *o, const float *a, const float *w, int n, int d) {
    for(int i=0;i<d;i++) {
        float s=0.0f;
        for(int j=0;j<n;j++) s += w[i*n+j]*a[j];
        o[i]=s;
    }
}
static void norm(float *o,const float *a,const float *w) {
    float s=0.0f;
    for(int i=0;i<DIM;i++) s+=a[i]*a[i];
    s=1.0f/sqrtf(s/(float)DIM+1.0e-5f);
    for(int i=0;i<DIM;i++) o[i]=w[i]*(s*a[i]);
}
static void softmax(float *a,int n) {
    float m=a[0],s=0.0f;
    for(int i=1;i<n;i++) if(a[i]>m) m=a[i];
    for(int i=0;i<n;i++) { a[i]=expf(a[i]-m); s+=a[i]; }
    for(int i=0;i<n;i++) a[i]/=s;
}
int ts_forward(int token,int pos) {
    if(token<0 || token>=VOCAB || pos<0 || pos>=CONTEXT) return -1;
    for(int i=0;i<DIM;i++) x[i]=emb[token*DIM+i];
    for(int l=0;l<LAYERS;l++) {
        float *k=keys[l][pos], *v=values[l][pos];
        norm(xb,x,ra+l*DIM);
        mm(q,xb,wq+l*DIM*DIM,DIM,DIM);
        mm(k,xb,wk+l*DIM*KV,DIM,KV);
        mm(v,xb,wv+l*DIM*KV,DIM,KV);
        for(int i=0;i<DIM;i+=2) {
            float c=rope[pos][(i%8)/2][0], s=rope[pos][(i%8)/2][1];
            float a=q[i], b=q[i+1]; q[i]=a*c-b*s; q[i+1]=a*s+b*c;
            if(i<KV) { a=k[i]; b=k[i+1]; k[i]=a*c-b*s; k[i+1]=a*s+b*c; }
        }
        for(int h=0;h<HEADS;h++) {
            for(int t=0;t<=pos;t++) {
                float score=0.0f;
                for(int i=0;i<8;i++) score+=q[h*8+i]*keys[l][t][(h/2)*8+i];
                att[t]=score*0.3535533905932738f;
            }
            softmax(att,pos+1);
            for(int i=0;i<8;i++) {
                float s=0.0f;
                for(int t=0;t<=pos;t++) s+=att[t]*values[l][t][(h/2)*8+i];
                xb[h*8+i]=s;
            }
        }
        mm(xb2,xb,wo+l*DIM*DIM,DIM,DIM);
        for(int i=0;i<DIM;i++) x[i]+=xb2[i];
        norm(xb,x,rf+l*DIM);
        mm(hb,xb,w1+l*DIM*HIDDEN,DIM,HIDDEN);
        mm(hb2,xb,w3+l*DIM*HIDDEN,DIM,HIDDEN);
        for(int i=0;i<HIDDEN;i++) hb[i]=(hb[i]/(1.0f+expf(-hb[i])))*hb2[i];
        mm(xb, hb,w2+l*HIDDEN*DIM,HIDDEN,DIM);
        for(int i=0;i<DIM;i++) x[i]+=xb[i];
    }
    norm(xb,x,rfinal);
    mm(logits,xb,emb,DIM,VOCAB);
    int best=0;
    for(int i=1;i<VOCAB;i++) if(logits[i]>logits[best]) best=i;
    return best;
}
const float *ts_logits(void) { return logits; }
