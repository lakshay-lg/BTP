import re, json, sys
NUM=re.compile(r'^[\d,]+(\.\d+)?$')
ITEM=re.compile(r'^\d{1,2}(\.\d{1,3}[A-Z]?){1,3}$')
START=re.compile(r'^Details? of cost (?:for|of)\s*([\d.,]+|one\b|1\b)?\s*(.*)$',re.I)
def cat(code):
    c=int(code)
    return 'LABOUR' if 100<=c<=169 else 'MACHINERY' if c<100 else 'SUNDRY' if c==9999 else 'MATERIAL'
def isnum(s): return bool(NUM.match(s))
def f(s): return float(s.replace(',',''))
def load(path,minpage):
    L=[];pg=0
    for l in open(path).read().split('\n'):
        l=l.strip()
        if l.startswith('=====PAGE'): pg=int(re.findall(r'\d+',l)[0]); continue
        if l and pg>=minpage: L.append((l,pg))
    return L
def parse(path,minpage=88):
    L=load(path,minpage); n=len(L)
    starts=[i for i,(l,_) in enumerate(L) if START.match(l)]
    parents={}; items=[]
    def item_ids_before(k,lo):
        # collect (idx,id) for item-number lines between lo and k
        out=[]
        for j in range(lo,k):
            l=L[j][0]
            if ITEM.match(l) and int(l.split(".")[0])<=26 and j+1<n and not isnum(L[j+1][0]):
                pj=L[j-1][0] if j>0 else ''
                if pj.startswith(('Add','TOTAL','Cost of','Say')) or pj in('W','X','Y','Z'): continue
                out.append((j,l))
        return out
    prev_end=0
    for si,s in enumerate(starts):
        end=starts[si+1] if si+1<len(starts) else n
        # header region: from prev_end to s
        ids=item_ids_before(s,prev_end)
        leaf=ids[-1] if ids else None
        # parents: earlier ids in region with their text
        for a,(j,idv) in enumerate(ids[:-1]):
            nxt=ids[a+1][0]
            txt=' '.join(x[0] for x in L[j+1:nxt] if 'SUB HEAD' not in x[0])
            parents[idv]=txt
        sub=''
        if leaf:
            j=leaf[0]; stop=j+1
            while stop<s and L[stop][0]!='Code': stop+=1
            sub=' '.join(x[0] for x in L[j+1:stop] if 'SUB HEAD' not in x[0])
        m=START.match(L[s][0])
        it={'id':leaf[1] if leaf else None,'page':L[s][1],'sub_desc':sub,'basis':{'qty':(1.0 if (m.group(1) or 'one').lower() in ('one','1') else f(m.group(1).rstrip('.'))),'unit':m.group(2).strip().rstrip('.')},'notes':[],'resources':[],'say_rate':None}
        i=s+1; section=None
        while i<end:
            l=L[i][0]
            if l in('MACHINERY','LABOUR','MATERIAL','MATERIALS','MATERIAL '): section=l.strip(); i+=1; continue
            if l=='Say':
                k=i+1
                if k<n and isnum(L[k][0]): it['say_rate']=f(L[k][0]); i=k+1
                else: i+=1
                prev_end=i; break
            if re.match(r'^\d{4}$',l):
                j=i+1; d=[]
                while j<i+14 and j+3<n:
                    if not isnum(L[j][0]) and isnum(L[j+1][0]) and isnum(L[j+2][0]) and isnum(L[j+3][0]):
                        it['resources'].append({'section':cat(l),'code':l,'desc':' '.join(d),'unit':L[j][0],'qty':f(L[j+1][0]),'rate':f(L[j+2][0]),'amount':f(L[j+3][0])})
                        i=j+4; break
                    d.append(L[j][0]); j+=1
                else: i+=1
                continue
            if section is None and not l.startswith(('TOTAL',)) : it['notes'].append(l)
            i+=1
        else:
            prev_end=end
        items.append(it)
    for it in items:
        if it['id']:
            p='.'.join(it['id'].split('.')[:2]); it['item_desc']=parents.get(p) or parents.get(it['id'])
    return items
if __name__=='__main__':
    it=parse(sys.argv[1]); json.dump(it,open(sys.argv[2],'w'),indent=1)
    print(len(it),'noid',sum(1 for x in it if not x['id']),'nores',sum(1 for x in it if not x['resources']),'nosay',sum(1 for x in it if x['say_rate'] is None),'nobasis',sum(1 for x in it if not x['basis']))
