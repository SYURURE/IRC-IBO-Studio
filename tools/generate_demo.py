"""Create fictional geometry-only display data; no quantum chemistry calculation."""
from pathlib import Path
import math

def generate(destination):
    lines=[]
    for frame in range(71):
        half=math.radians(49 + 6*frame/70)
        length=0.96
        x,y=length*math.sin(half),length*math.cos(half)
        lines.extend(['3',f'SYNTHETIC_DEMO frame={frame}; fictional geometry; NOT IRC or computed data',
                      'O 0.00000000 0.00000000 0.00000000',
                      f'H {x:.8f} {y:.8f} 0.00000000',
                      f'H {-x:.8f} {y:.8f} 0.00000000'])
    Path(destination).write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':
    generate(Path(__file__).resolve().parents[1]/'examples/synthetic_water_bend.xyz')
