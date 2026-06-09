import urllib.request
import re
url = 'https://scorelib.vercel.app/'
html = urllib.request.urlopen(urllib.request.Request(url), timeout=20).read().decode('utf-8', errors='ignore')
print(html[:800])
matches = re.findall(r'src="([^"]+\.js)"|href="([^"]+\.css)"', html)
print('ASSET PATHS', [m[0] or m[1] for m in matches][:20])
