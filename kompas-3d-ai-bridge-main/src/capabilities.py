from __future__ import annotations
import shutil
import time
from pathlib import Path
from protocol import ok,fail
from safety import safe_write,copy_destination
from screenshot import capture
from jobs import run_job
PDF_DLL=Path(r'C:\Program Files\ASCON\KOMPAS-3D v25\Bin\Pdf2d.dll')
def _drawing(s):
 import win32com.client as wc
 d=_doc(s); d2=wc.CastTo(d,'IKompasDocument2D'); view=d2.ViewsAndLayersManager.Views.View(0)
 return d, wc.CastTo(view,'IDrawingContainer')
def _update(obj): obj.Update(); return obj
def _doc(s):
 d=s.active()
 if d is None: raise RuntimeError('no_active_document')
 return d
def _path(s,d): return s.document_path(d) or ''
def _write_active(s,d): return safe_write(s.last_document_path or _path(s,d),s.root)
def kompas_status(s,p): return ok('kompas.status',s.status())
def documents_list(s,p): return ok('documents.list',{'documents':s.documents()})
def document_active(s,p):
 d=s.active(); path=s.document_path(d) if d else None; return ok('document.active',{'present':d is not None,'name':str(getattr(d,'Name','')) if d else None,'file_name':path,'path_name':path})
def _open_copy_with_rebuild_yes(s,p,action):
 source=Path(p['source']); dst=copy_destination(source,s.root,p.get('name')); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,dst)
 app=s.connect()
 # KOMPAS SDK ksHideMessageEnum:
 #   ksShowMessage=0
 #   ksHideMessageYes=1 -> suppress Yes/No dialog choosing Yes, with rebuild.
 # We use it ONLY while opening the disposable COPY, then always restore UI messages.
 try:
  app.HideMessage=1
 except Exception:
  pass
 try:
  visible=bool(p.get('visible',False)); doc=app.Documents.Open(str(dst),visible,False)
  try:
   doc.Active=True
  except Exception:
   try:
    doc.SetActive(True)
   except Exception:
    pass
  time.sleep(0.35)
 finally:
  try:
   app.HideMessage=0
  except Exception:
   pass
 s.last_document_path=str(dst)
 active_now=s.active()
 active_path=s.document_path(active_now) if active_now else None
 if not active_now or (active_path and Path(active_path).name.lower()!=dst.name.lower()):
  raise RuntimeError(f"opened_copy_did_not_become_active: copy={dst}; active={active_path}")
 return ok(action,{'source':str(source),'copy':str(dst),'file_name':(s.document_path(doc) or str(dst)),'active_path':active_path,'rebuild_prompt_policy':'auto_yes_on_copy_only'})

def document_open_copy(s,p):
 return _open_copy_with_rebuild_yes(s,p,'document.open_copy')

def model_open_copy(s,p):
 return _open_copy_with_rebuild_yes(s,p,'model.open_copy')
def model_info(s,p):
 import win32com.client as wc
 d=_doc(s); partdoc=wc.CastTo(d,'IPartDocument'); top=partdoc.TopPart; part7=wc.CastTo(top,'IPart7'); container=wc.CastTo(part7,'IModelContainer')
 return ok('model.info',{'top_part_present':top is not None,'model_container_present':container is not None,'file_name':s.last_document_path})
def model_block_update(s,p):
 import win32com.client as wc
 d=_doc(s); _write_active(s,d); top=wc.CastTo(wc.CastTo(d,'IPartDocument').TopPart,'IPart7'); container=wc.CastTo(top,'IModelContainer')
 block=wc.CastTo(container.ElementaryBodies.Add(668),'IBlockBySizes')
 for name in ('Length','Width'):
  if name.lower() in p: setattr(block,name,float(p[name.lower()]))
 if 'height' in p: block.SetHeight(True,float(p['height']))
 block.Update(); d.Save()
 return ok('model.block.update',{'index':int(p.get('index',0)),'length':block.Length,'width':block.Width,'height':block.Height(True)})
def model_block_read(s,p):
 import win32com.client as wc
 d=_doc(s); top=wc.CastTo(wc.CastTo(d,'IPartDocument').TopPart,'IPart7'); container=wc.CastTo(top,'IModelContainer'); bodies=container.ElementaryBodies
 index=int(p.get('index',bodies.Count-1)); block=wc.CastTo(bodies.ElementaryBody(index),'IBlockBySizes')
 return ok('model.block.read',{'count':bodies.Count,'index':index,'length':block.Length,'width':block.Width,'height':block.Height(True)})
def document_close(s,p):
 d=_doc(s); d.Close(getattr(d,'ksSaveChangesNo',0)); return ok('document.close',{'closed':True})
def document_save(s,p):
 d=_doc(s); _write_active(s,d); d.Save(); return ok('document.save',{'file_name':s.last_document_path or _path(s,d)})
def document_save_as(s,p):
 d=_doc(s); dest=safe_write(p['path'],s.root); d.SaveAs(str(dest)); return ok('document.save_as',{'path':str(dest)})
def sheet_info(s,p):
 d=_doc(s); sh=d.LayoutSheets.Item(0); f=sh.Format; return ok('sheet.info',{'sheet_count':d.LayoutSheets.Count,'format':{'width':getattr(f,'Width',None),'height':getattr(f,'Height',None),'format':getattr(f,'Format',None)}})
def system_form(s,p):
 d=_doc(s); return ok('system_form.inspect',{'document_type':str(getattr(d,'DocumentType','')),'file_name':_path(s,d)})
def objects_list(s,p):
 d=_doc(s); return ok('objects.list',{'document_type':str(getattr(d,'DocumentType','')),'note':'Typed drawing enumeration is available through future API5 extension.'})
def texts_list(s,p):
 _,c=_drawing(s); col=c.DrawingTexts; rows=[]
 import win32com.client as wc
 for i in range(col.Count):
  t=col.DrawingText(i); text=wc.CastTo(t,'IText'); rows.append({'index':i,'text':str(getattr(text,'Str','')),'x':getattr(t,'X',None),'y':getattr(t,'Y',None)})
 return ok('texts.list',{'count':len(rows),'texts':rows})
def dimensions_list(s,p):
 _,c=_drawing(s)
 import win32com.client as wc
 sc=wc.CastTo(c,'ISymbols2DContainer')
 return ok('dimensions.list',{'linear_count':sc.LineDimensions.Count,'radial_count':sc.RadialDimensions.Count})
def text_create(s,p):
 d,c=_drawing(s); _write_active(s,d)
 import win32com.client as wc
 raw=c.DrawingTexts.Add(); raw.X=float(p['x']); raw.Y=float(p['y']); t=wc.CastTo(raw,'IText'); t.Str=str(p['text'])
 try:
  f=wc.CastTo(t.TextLine(0).TextItem(0),'ITextFont'); f.Height=float(p.get('height',5)); f.FontName=str(p.get('font','GOST type BU'))
 except Exception: pass
 _update(raw); return ok('text.create',{'index':c.DrawingTexts.Count-1,'text':str(p['text']),'x':raw.X,'y':raw.Y})
def text_edit(s,p):
 _,c=_drawing(s); t=c.DrawingTexts.DrawingText(int(p['index'])); t.Str=str(p['text']); _update(t); return ok('text.edit',{'index':int(p['index']),'text':str(p['text'])})
def text_move(s,p):
 _,c=_drawing(s); t=c.DrawingTexts.DrawingText(int(p['index'])); t.X=float(p['x']); t.Y=float(p['y']); _update(t); return ok('text.move',{'index':int(p['index']),'x':t.X,'y':t.Y})
def text_delete(s,p):
 _,c=_drawing(s); t=c.DrawingTexts.DrawingText(int(p['index'])); t.Delete(); return ok('text.delete',{'index':int(p['index'])})
def _primitive(s,p,kind):
 d,c=_drawing(s); _write_active(s,d); col=getattr(c,{'line':'Lines','circle':'Circles','arc':'Arcs'}[kind]); o=col.Add()
 for k,v in p.items():
  key={'x1':'X1','y1':'Y1','x2':'X2','y2':'Y2','x3':'X3','y3':'Y3','xc':'Xc','yc':'Yc','radius':'Radius','style':'Style','direction':'Direction'}.get(k)
  if key: setattr(o,key,v)
 _update(o); return ok(f'{kind}.create',{'index':col.Count-1,'properties':p})
def line_create(s,p): return _primitive(s,p,'line')
def circle_create(s,p): return _primitive(s,p,'circle')
def arc_create(s,p): return _primitive(s,p,'arc')
def linear_dimension(s,p):
 d,c=_drawing(s); _write_active(s,d)
 import win32com.client as wc
 dims=wc.CastTo(c,'ISymbols2DContainer').LineDimensions; dim=dims.Add()
 for k in ('X1','Y1','X2','Y2','X3','Y3','Orientation','Angle'):
  q=k.lower(); setattr(dim,k,p.get(q,p.get(k))) if q in p or k in p else None
 _update(dim); return ok('dimension.linear.create',{'index':dims.Count-1})
def radial_dimension(s,p):
 d,c=_drawing(s); _write_active(s,d)
 import win32com.client as wc
 dims=wc.CastTo(c,'ISymbols2DContainer').RadialDimensions; dim=dims.Add()
 for k in ('Xc','Yc','X1','Y1','X2','Y2','X3','Y3'):
  q=k.lower(); setattr(dim,k,p.get(q,p.get(k))) if q in p or k in p else None
 _update(dim); return ok('dimension.radial.create',{'index':dims.Count-1})
def screenshot(s,p): return ok('screenshot',capture(s.root))
def export_pdf(s,p):
 d=_doc(s); source=s.last_document_path or _path(s,d); dest=safe_write(p['path'],s.root)
 if not PDF_DLL.is_file(): raise FileNotFoundError(str(PDF_DLL))
 dest.parent.mkdir(parents=True,exist_ok=True); result=s.connect().Converter(str(PDF_DLL)).Convert(str(source),str(dest),int(p.get('command',0)),False)
 data=dest.read_bytes() if dest.exists() else b''
 if not data.startswith(b'%PDF-'): raise RuntimeError(f'pdf_validation_failed: converter={result}, bytes={len(data)}')
 import pymupdf
 pdf=pymupdf.open(str(dest)); pages=pdf.page_count; pdf.close()
 return ok('export.pdf',{'path':str(dest),'bytes':len(data),'pages':pages,'converter_result':result})
def bbox(s,p):
 d=_doc(s); return ok('object.bbox',{'bbox':str(d.GetObjectsGabarit())})
def stamp_read(s,p):
 d=_doc(s); sh=d.LayoutSheets.Item(0); st=sh.Stamp; cells=p.get('cells',[1,2,3]); return ok('stamp.read',{'cells':{str(i):str(st.Text(i).Str) for i in cells}})
def stamp_write(s,p):
 d=_doc(s); safe_write(_path(s,d),s.root); st=d.LayoutSheets.Item(0).Stamp
 for k,v in p['cells'].items(): st.Text(int(k)).Str=str(v)
 st.Update(); return ok('stamp.write',{'cells':p['cells']})
registry={'kompas.status':kompas_status,'documents.list':documents_list,'document.active':document_active,'document.open_copy':document_open_copy,'model.open_copy':model_open_copy,'model.info':model_info,'model.block.update':model_block_update,'model.block.read':model_block_read,'document.close':document_close,'document.save':document_save,'document.save_as':document_save_as,'sheet.info':sheet_info,'system_form.inspect':system_form,'objects.list':objects_list,'texts.list':texts_list,'dimensions.list':dimensions_list,'text.create':text_create,'text.edit':text_edit,'text.move':text_move,'text.delete':text_delete,'line.create':line_create,'circle.create':circle_create,'arc.create':arc_create,'dimension.linear.create':linear_dimension,'dimension.radial.create':radial_dimension,'object.bbox':bbox,'stamp.read':stamp_read,'stamp.write':stamp_write,'screenshot':screenshot,'export.pdf':export_pdf}
registry['run_job']=run_job
