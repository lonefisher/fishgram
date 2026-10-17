"""Render FishGram's geometric vector mark to Windows PNG/ICO assets.

Development-only tool; runtime never needs Python or Pillow. The SVG is the
editable master and all output is stored under Resources/fishgram.
"""
from pathlib import Path
import argparse
from PIL import Image, ImageDraw


def render(size=1024, margin=True, monochrome=False):
    scale = size / 512
    image = Image.new('RGBA', (size, size))
    draw = ImageDraw.Draw(image)
    def coordinates(points):
        return [(round(x * scale), round(y * scale)) for x, y in points]
    def bezier(points, steps=80):
        a, b, c, d = points
        return [((1-t)**3*a[0]+3*(1-t)**2*t*b[0]+3*(1-t)*t*t*c[0]+t**3*d[0],
                 (1-t)**3*a[1]+3*(1-t)**2*t*b[1]+3*(1-t)*t*t*c[1]+t**3*d[1])
                for t in (i/steps for i in range(steps+1))]
    teal = '#087f8c'
    if not monochrome:
        inset = 16 if margin else 0
        draw.ellipse((inset*scale, inset*scale, (512-inset)*scale, (512-inset)*scale), fill=teal)
    body = bezier([(352,167),(290,112),(192,146),(156,213)])
    body += [(88,178),(88,332),(156,297)]
    body += bezier([(156,297),(192,364),(290,398),(352,343)])
    body += bezier([(352,343),(394,306),(424,277),(424,255)])
    body += bezier([(424,255),(424,233),(394,204),(352,167)])
    draw.polygon(coordinates(body), fill='white')
    draw.polygon(coordinates([(229,150),(268,99),(299,151)]), fill='white')
    draw.polygon(coordinates([(229,360),(268,411),(299,359)]), fill='white')
    draw.ellipse((336*scale,217*scale,366*scale,247*scale), fill=(0,0,0,0) if monochrome else teal)
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=Path(__file__).resolve().parent.parent)
    options = parser.parse_args()
    directory = Path(options.root) / 'tdesktop/Telegram/Resources/fishgram'
    directory.mkdir(exist_ok=True)
    for filename, margin, size in [('logo_256.png',True,256),('logo_256_no_margin.png',False,256),('icon_round512@2x.png',True,1024)]:
        render(1024,margin).resize((size,size), Image.Resampling.LANCZOS).save(directory/filename)
    render().save(directory/'fishgram.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print('Rendered FishGram vector assets.')


if __name__ == '__main__':
    main()
