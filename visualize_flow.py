"""Draw Fig.3.1 control flow and color it with recorded workflow statuses."""
import json
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR', str(Path(__file__).resolve().parent/'.cache'/'matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon

def draw(output='results'):
    root = Path(__file__).resolve().parent
    out = root/output
    out.mkdir(exist_ok=True, parents=True)
    log = json.loads((out/'workflow.json').read_text()) if (out/'workflow.json').exists() else []
    status = {x['phase']: x['status'] for x in log}
    fig, ax = plt.subplots(figsize=(15,7))
    ax.set_xlim(-1,16); ax.set_ylim(-1,7); ax.axis('off')
    def box(name, x,y,w=2,h=1):
        s=status.get(name,'')
        c='#d4eedc' if s in ('passed','completed','demo completed') else '#fff0c2' if s in ('partial','skipped') else '#e8edf4'
        ax.add_patch(FancyBboxPatch((x-w/2,y-h/2),w,h,boxstyle='round,pad=0.04',facecolor=c,edgecolor='#223348',lw=1.6))
        ax.text(x,y,name+ ('\n'+s if s else ''),ha='center',va='center',fontsize=10)
    def diamond(x,y,label,w=3,h=2):
        ax.add_patch(Polygon([(x-w/2,y),(x,y+h/2),(x+w/2,y),(x,y-h/2)],facecolor='#edf3fa',edgecolor='#223348',lw=1.6))
        ax.text(x,y,label,ha='center',va='center',fontsize=9)
    def arrow(points,label=None):
        for a,b in zip(points[:-2],points[1:-1]): ax.plot([a[0],b[0]],[a[1],b[1]],color='#223348')
        ax.annotate('',xy=points[-1],xytext=points[-2],arrowprops={'arrowstyle':'->','color':'#223348','lw':1.5})
        if label: ax.text(*points[0],label,fontsize=10,va='bottom')
    box('START',0,5,1.4); box('Phase 1',2.6,5); diamond(6,5,'Models converge\nand baselines saved?')
    box('Phase 2',9.5,5); box('Phase 3',13,5)
    diamond(13,2.8,'Computing budget\nfor spacing?')
    box('Phase 4',13,0); box('Phase 5',9.5,0); box('Data Analysis',6,0); box('END',2.6,0)
    arrow([(.7,5),(1.6,5)]); arrow([(3.6,5),(4.5,5)])
    arrow([(7.5,5),(8.5,5)],'YES'); arrow([(10.5,5),(12,5)])
    arrow([(6,4),(6,3),(2.6,3),(2.6,4.5)],'NO / bounded retry')
    arrow([(13,4.5),(13,3.8)]); arrow([(13,1.8),(13,.5)],'YES')
    arrow([(11.5,2.8),(9.5,2.8),(9.5,.5)],'NO')
    arrow([(12,0),(10.5,0)]); arrow([(8.5,0),(7,0)]); arrow([(5,0),(3.6,0)])
    ax.set_title('Fig. 3.1 — executable workflow\nDemonstration status; research validation tracked separately',fontsize=15)
    fig.tight_layout(); fig.savefig(out/'flowchart.png',dpi=180); fig.savefig(out/'flowchart.svg'); plt.close(fig)

if __name__=='__main__':
    import sys
    draw(sys.argv[1] if len(sys.argv)>1 else 'results')
