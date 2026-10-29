import math

class Component:
    def __init__(self, name='comp'):
        self.name=name; self.items=[]  # boxes and refs
        self.boxes=[]
    def add_ref(self, comp, *args, **kwargs):
        ref=Ref(comp); self.items.append(ref); return ref
    def add_array(self, component, columns=1, rows=1, spacing=(0,0), **kwargs):
        arr=ArrayRef(component, int(columns), int(rows), spacing); self.items.append(arr); return arr
    def add(self, item): self.items.append(item); return item
    def add_label(self, text, position=(0,0), layer=(68,5), **kwargs):
        self.items.append(Label(text, position, layer))
    def bbox(self):
        xs=[]; ys=[]
        for it in self.items:
            b=it.bbox()
            if b: xs += [b[0],b[2]]; ys += [b[1],b[3]]
        for b in self.boxes: xs += [b[0],b[2]]; ys += [b[1],b[3]]
        return (min(xs),min(ys),max(xs),max(ys)) if xs else (0,0,0,0)
    @property
    def xmin(self): return self.bbox()[0]
    @property
    def xmax(self): return self.bbox()[2]
    @property
    def ymin(self): return self.bbox()[1]
    @property
    def ymax(self): return self.bbox()[3]
    def move(self, p): self.movex(p[0]); self.movey(p[1]); return self
    def movex(self, dx):
        for it in self.items: it.movex(dx)
        self.boxes=[(x1+dx,y1,x2+dx,y2) for (x1,y1,x2,y2) in self.boxes]
        return self
    def movey(self, dy):
        for it in self.items: it.movey(dy)
        self.boxes=[(x1,y1+dy,x2,y2+dy) for (x1,y1,x2,y2) in self.boxes]
        return self

class Ref:
    def __init__(self, comp): self.comp=comp; self.dx=0; self.dy=0
    def move(self,p): self.dx+=p[0]; self.dy+=p[1]; return self
    def movex(self,dx): self.dx+=dx; return self
    def movey(self,dy): self.dy+=dy; return self
    def bbox(self):
        x1,y1,x2,y2=self.comp.bbox(); return (x1+self.dx,y1+self.dy,x2+self.dx,y2+self.dy)
    @property
    def xmin(self): return self.bbox()[0]
    @property
    def xmax(self): return self.bbox()[2]
    @property
    def ymin(self): return self.bbox()[1]
    @property
    def ymax(self): return self.bbox()[3]

class ArrayRef(Ref):
    def __init__(self, comp, cols, rows, spacing):
        super().__init__(comp); self.cols=cols; self.rows=rows; self.sx=spacing[0]; self.sy=spacing[1]
    def bbox(self):
        x1,y1,x2,y2=self.comp.bbox(); return (x1+self.dx, y1+self.dy,
            x2+self.dx+(self.cols-1)*self.sx, y2+self.dy+(self.rows-1)*self.sy)

class Label:
    def __init__(self,text,pos,layer): self.text=text; self.pos=pos; self.layer=layer; self.dx=0; self.dy=0
    def movex(self,dx): self.dx+=dx
    def movey(self,dy): self.dy+=dy
    def bbox(self): return None

class _Components:
    @staticmethod
    def rectangle(size, layer):
        c=Component('rect'); c.boxes=[(0.0,0.0,float(size[0]),float(size[1]))]; c.layer=layer; return c

components=_Components()

def cell(fn=None, **kwargs):
    if fn is None: return lambda f:f
    return fn

def boolean(A,B,operation,layer):
    raise NotImplementedError('boolean shim not implemented')

def open_component(name): return Component(name)
def take_component(c, target_cell):
    import pya
    def emit_box(x1,y1,x2,y2,layer):
        li=target_cell.layout().layer(int(layer[0]),int(layer[1]))
        target_cell.shapes(li).insert(pya.Box(int(round(x1*1000)),int(round(y1*1000)),
                                             int(round(x2*1000)),int(round(y2*1000))))
    def walk(c,dx=0,dy=0):
        layer=getattr(c,'layer',(68,20))
        for b in c.boxes: emit_box(b[0]+dx,b[1]+dy,b[2]+dx,b[3]+dy,layer)
        for it in c.items:
            if isinstance(it,ArrayRef):
                for r in range(it.rows):
                    for col in range(it.cols):
                        walk(it.comp,dx+it.dx+col*it.sx,dy+it.dy+r*it.sy)
            elif isinstance(it,Ref):
                walk(it.comp,dx+it.dx,dy+it.dy)
            elif isinstance(it,Label):
                li=target_cell.layout().layer(int(it.layer[0]),int(it.layer[1]))
                target_cell.shapes(li).insert(pya.Text(it.text,pya.Trans(pya.Vector(int(round((it.pos[0]+it.dx+dx)*1000)),int(round((it.pos[1]+it.dy+dy)*1000))))))
    walk(c)

class Typing:
    LayerSpec=tuple
    Float2=tuple

import sys, types
# Register a fake gdsfactory module for imports.
gf=types.ModuleType('gdsfactory'); gf.Component=Component; gf.components=components; gf.cell=cell; gf.boolean=boolean
sys.modules['gdsfactory']=gf
ty=types.ModuleType('gdsfactory.typings'); ty.LayerSpec=tuple; ty.Float2=tuple
sys.modules['gdsfactory.typings']=ty
