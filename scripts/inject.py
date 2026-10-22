import base64, sys
tpl, gz, out = sys.argv[1:4]
b = base64.b64encode(open(gz, 'rb').read()).decode()
open(out, 'w').write(open(tpl).read().replace('__DATA__', b))
