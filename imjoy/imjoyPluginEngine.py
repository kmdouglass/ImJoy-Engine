import os
import asyncio
import socketio
import logging
import threading
import sys
import traceback
import time
import subprocess
import signal
import random
import string
import shlex
import logging
import argparse
import uuid
import shutil
import webbrowser
from aiohttp import web, hdrs
from aiohttp import WSCloseCode
from aiohttp import streamer
from urllib.parse import urlparse
from mimetypes import MimeTypes
try:
    import psutil
except Exception as e:
    print("WARNING: a library called 'psutil' can not be imported, this may cause problem when killing processes.")

try:
    from Queue import Queue, Empty
except ImportError:
    from queue import Queue, Empty  # python 3.x

# add executable path to PATH
os.environ['PATH'] = os.path.split(sys.executable)[0]  + os.pathsep +  os.environ.get('PATH', '')


try:
    subprocess.call(["conda", "-V"])
except OSError as e:
    CONDA_AVAILABLE = False
    if sys.version_info < (3, 0):
        sys.exit('Sorry, ImJoy plugin engine can only run within a conda environment or at least in Python 3.')
    print('WARNING: you are running ImJoy without conda, you may have problem with some plugins.')
else:
    CONDA_AVAILABLE = True

logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger('ImJoyPluginEngine')

parser = argparse.ArgumentParser()
parser.add_argument('--token', type=str, default=None, help='connection token')
parser.add_argument('--debug', action="store_true", help='debug mode')
parser.add_argument('--serve', action="store_true", help='download ImJoy web app and serve it locally')
parser.add_argument('--host', type=str, default='127.0.0.1', help='socketio host')
parser.add_argument('--port', type=str, default='8080', help='socketio port')
parser.add_argument('--force_quit_timeout', type=int, default=5, help='the time (in second) for waiting before kill a plugin process, default: 5 s')
parser.add_argument('--workspace', type=str, default='~/ImJoyWorkspace', help='workspace folder for plugins')
parser.add_argument('--freeze', action="store_true", help='disable conda and pip commands')

opt = parser.parse_args()

if not CONDA_AVAILABLE and not opt.freeze:
    print('WARNING: `pip install` command may not work, in that case you may want to add "--freeze".')

if opt.freeze:
    print('WARNING: you are running the plugin engine with `--freeze`, this means you need to handle all the plugin requirements yourself.')

FORCE_QUIT_TIMEOUT = opt.force_quit_timeout
WORKSPACE_DIR = os.path.expanduser(opt.workspace)
if not os.path.exists(WORKSPACE_DIR):
    os.makedirs(WORKSPACE_DIR)

# generate a new token if not exist
try:
    if opt.token is None or opt.token == "":
        with open(os.path.join(WORKSPACE_DIR, '.token'), 'r') as f:
            opt.token = f.read()
except Exception as e:
    pass

try:
    if opt.token is None or opt.token == "":
        opt.token = str(uuid.uuid4())
        with open(os.path.join(WORKSPACE_DIR, '.token'), 'w') as f:
            f.write(opt.token)
except Exception as e:
    logger.error('Falied to save .token file: %s', str(e))

def killProcess(pid):
    try:
        cp = psutil.Process(pid)
        for proc in cp.children(recursive=True):
            proc.kill()
        cp.kill()
    except Exception as e:
        print("WARNING: failed to kill a process (PID={}), you may want to kill it manually.".format(pid))

# try to kill last process
pid_file = os.path.join(WORKSPACE_DIR, '.pid')
try:
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            killProcess(int(f.read()))
except Exception as e:
    pass
try:
    pid = str(os.getpid())
    with open(pid_file, 'w') as f:
        f.write(pid)
except Exception as e:
    logger.error('Falied to save .pid file: %s', str(e))

WEB_APP_DIR = os.path.join(WORKSPACE_DIR, '__ImJoy__')
if opt.serve:
    if shutil.which('git') is None:
        print('Installing git...')
        ret = subprocess.Popen("conda install -y git && git clone -b gh-pages --depth 1 https://github.com/oeway/ImJoy".split(), shell=False).wait()
        if ret != 0:
            print('Failed to install git, please check whether you have internet access.')
            sys.exit(3)
    if os.path.exists(WEB_APP_DIR) and os.path.isdir(WEB_APP_DIR):
        ret = subprocess.Popen(['git', 'pull', '--all'], cwd=WEB_APP_DIR, shell=False).wait()
        # "subprocess.Popen can not recongnize '&&' after 'git pull' with nothing to add "
        if ret != 0:
            print('Failed to pull files for serving offline.')
        ret = subprocess.Popen(['git', 'checkout', 'gh-pages'], cwd=WEB_APP_DIR, shell=False).wait()
        if ret != 0:
            print('Failed to pull files for serving offline.')
            #shutil.rmtree(WEB_APP_DIR)
    if not os.path.exists(WEB_APP_DIR):
        print('Downloading files for serving ImJoy locally...')
        ret = subprocess.Popen('git clone -b gh-pages --depth 1 https://github.com/oeway/ImJoy __ImJoy__'.split(), shell=False, cwd=WORKSPACE_DIR).wait()
        if ret != 0:
            print('Failed to download files, please check whether you have internet access.')
            sys.exit(4)

MAX_ATTEMPTS = 1000
NAME_SPACE = '/'
# ALLOWED_ORIGINS = ['http://'+opt.host+':'+opt.port, 'http://imjoy.io', 'https://imjoy.io']
sio = socketio.AsyncServer()
app = web.Application()
sio.attach(app)

if opt.debug:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.ERROR)


if opt.serve and os.path.exists(os.path.join(WEB_APP_DIR, 'index.html')):
    async def index(request):
        """Serve the client-side application."""
        with open(os.path.join(WEB_APP_DIR, 'index.html'), 'r', encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type='text/html')
    app.router.add_static('/static', path=str(os.path.join(WEB_APP_DIR, 'static')))
    # app.router.add_static('/docs/', path=str(os.path.join(WEB_APP_DIR, 'docs')))
    async def docs_handler(request):
        raise web.HTTPFound(location='https://imjoy.io/docs')
    app.router.add_get('/docs', docs_handler, name='docs')
    print('A local version of Imjoy web app is available at http://127.0.0.1:8080')
else:
    async def index(request):
        return web.Response(body='<H1><a href="https://imjoy.io">ImJoy.IO</a></H1><p>You can run "python -m imjoy --serve" to serve ImJoy web app locally.</p>', content_type="text/html")
app.router.add_get('/', index)

async def about(request):
    params = request.rel_url.query
    if 'token' in params:
        body = '<H1>ImJoy Plugin Engine connection token: </H1><H3>'+params['token'] + '</H3><br>'
        body += '<p>You have to specify this token when you connect the ImJoy web app to this Plugin Engine. The token will be saved and automatically reused when you launch the App again. </p>'
        body += '<br>'
        body += '<p>Alternatively, you can launch a new ImJoy instance with the link below: </p>'

        if opt.serve:
            body += '<p><a href="http://127.0.0.1:8080/#/app?token='+params['token']+'">Open ImJoy App</a></p>'
        else:
            body += '<p><a href="https://imjoy.io/#/app?token='+params['token']+'">Open ImJoy App</a></p>'

    else:
        if opt.serve:
            body = '<H1><a href="http://127.0.0.1:8080/#/app">Open ImJoy App</a></H1>'
        else:
            body = '<H1><a href="https://imjoy.io/#/app">Open ImJoy App</a></H1>'
    body += '<H2>Please use the latest Google Chrome browser to run the ImJoy App.</H2><a href="https://www.google.com/chrome/">Download Chrome</a><p>Note: Safari is not supported due to its restrictions on connecting to localhost. Currently, only FireFox and Chrome (preferred) are supported.</p>'
    return web.Response(body=body, content_type="text/html")
app.router.add_get('/about', about)

attempt_count = 0

cmd_history = []
default_requirements_py2 = ["requests", "six", "websocket-client", "numpy", "psutil"]
default_requirements_py3 = ["requests", "six", "websocket-client", "janus", "numpy", "psutil"]

script_dir = os.path.dirname(os.path.normpath(__file__))
template_script = os.path.abspath(os.path.join(script_dir, 'imjoyWorkerTemplate.py'))

if sys.platform == "linux" or sys.platform == "linux2":
    # linux
    command_template = '/bin/bash -c "source {}/bin/activate"'
    conda_activate = command_template.format("$(conda info --json -s | python -c \"import sys, json; print(json.load(sys.stdin)['conda_prefix']);\")")
elif sys.platform == "darwin":
    # OS X
    conda_activate = "source activate"
elif sys.platform == "win32":
    # Windows...
    conda_activate = "activate"
else:
    conda_activate = "conda activate"

plugins = {}
plugin_sessions = {}
plugin_sids = {}
plugin_signatures = {}
clients = {}
client_sessions = {}
registered_sessions = {}

def resumePluginSession(pid, session_id, plugin_signature):
    if pid in plugins:
        if session_id in plugin_sessions:
            plugin_sessions[session_id].append(plugins[pid])
        else:
            plugin_sessions[session_id] = [plugins[pid]]
    if plugin_signature in plugin_signatures:
        secret = plugin_signatures[plugin_signature]
        return secret
    else:
        return None

def addClientSession(session_id, client_id, sid):
    if client_id in clients:
        clients[client_id].append(sid)
        client_connected = True
    else:
        clients[client_id] = [sid]
        client_connected = False
    registered_sessions[sid] = (client_id, session_id)
    return client_connected

def disconnectClientSession(sid):
    tasks = []
    if sid in registered_sessions:
        client_id, session_id = registered_sessions[sid]
        del registered_sessions[sid]
        if client_id in clients and sid in clients[client_id]:
            clients[client_id].remove(sid)
            if len(clients[client_id]) == 0:
                del clients[client_id]
        if session_id in plugin_sessions:
            for plugin in plugin_sessions[session_id]:
                if 'allow-detach' not in plugin['flags']:
                    tasks.append(on_kill_plugin(sid, plugin))
            del plugin_sessions[session_id]
    return tasks

def addPlugin(plugin_info, sid=None):
    pid = plugin_info['id']
    session_id = plugin_info['session_id']
    plugin_signatures[plugin_info['signature']] = plugin_info['secret']
    plugins[pid] = plugin_info
    if session_id in plugin_sessions:
        plugin_sessions[session_id].append(plugin_info)
    else:
        plugin_sessions[session_id] = [plugin_info]

    if pid in plugins and sid is not None:
        plugin_sids[sid] = plugin_info
        plugin_info['sid'] = sid

def disconnectPlugin(sid):
    tasks = []
    if sid in plugin_sids:
        pid = plugin_sids[sid]['id']
        if pid in plugins:
            if plugins[pid]['signature'] in plugin_signatures:
                del plugin_signatures[plugins[pid]['signature']]
            del plugins[pid]
        del plugin_sids[sid]
        for session_id in plugin_sessions.keys():
            exist = False
            for p in plugin_sessions[session_id]:
                if p['id'] == pid:
                    exist = p
            if exist:
                plugin_sessions[session_id].remove(exist)
                tasks.append(on_kill_plugin(sid, exist))
    return tasks

def setPluginPID(plugin_id, pid):
    plugins[plugin_id]['process_id'] = pid

def killPlugin(pid):
    if pid in plugins:
        if plugins[pid]['signature'] in plugin_signatures:
            del plugin_signatures[plugins[pid]['signature']]
        try:
            plugins[pid]['abort'].set()
            killProcess(plugins[pid]['process_id'])
            print('INFO: "{}" was killed.'.format(pid))
        except Exception as e:
            print('WARNING: failed to kill plugin "{}".'.format(pid))
            logger.error(str(e))
        if 'sid' in plugins[pid]:
            if plugins[pid]['sid'] in plugin_sids:
                del plugin_sids[plugins[pid]['sid']]
        del plugins[pid]


def killAllPlugins():
    tasks = []
    for sid in plugin_sids:
        try:
            tasks.append(on_kill_plugin(sid, {"id":plugin_sids[sid]['id']}))
        finally:
            pass
    return asyncio.gather(*tasks)

@sio.on('connect', namespace=NAME_SPACE)
def connect(sid, environ):
    logger.info("connect %s", sid)

@sio.on('init_plugin', namespace=NAME_SPACE)
async def on_init_plugin(sid, kwargs):
    if sid in registered_sessions:
        client_id, session_id = registered_sessions[sid]
    else:
        logger.debug('client %s is not registered.', sid)
        return {'success': False}
    pid = kwargs['id']
    config = kwargs.get('config', {})
    env = config.get('env', None)
    cmd = config.get('cmd', 'python')
    pname = config.get('name', None)
    flags = config.get('flags', [])
    tag = config.get('tag', '')
    requirements = config.get('requirements', []) or []
    workspace = config.get('workspace', 'default')
    work_dir = os.path.join(WORKSPACE_DIR, workspace)
    if not os.path.exists(work_dir):
        os.makedirs(work_dir)
    plugin_env = os.environ.copy()
    plugin_env['WORK_DIR'] = work_dir

    logger.info("initialize the plugin. name=%s, id=%s, cmd=%s, workspace=%s", pname, id, cmd, workspace)

    plugin_signature = "{}/{}/{}".format(workspace, pname, tag)

    if 'single-instance' in flags:
        secret = resumePluginSession(pid, session_id, plugin_signature)
        if secret is not None:
            logger.debug('plugin already initialized: %s', pid)
            # await sio.emit('message_from_plugin_'+secret, {"type": "initialized", "dedicatedThread": True})
            return {'success': True, 'initialized': True, 'secret': secret, 'work_dir': os.path.abspath(work_dir)}

    env_name = ''
    is_py2 = False
    envs = None
    if env is not None:
        if not opt.freeze and CONDA_AVAILABLE:
            if type(env) is str:
                envs = [env]
            else:
                envs = env
            for i, env in enumerate(envs):
                if 'conda create' in env:
                    # if not env.startswith('conda'):
                    #     raise Exception('env command must start with conda')
                    if 'python=2' in env:
                        is_py2 = True
                    parms = shlex.split(env)
                    if '-n' in parms:
                        env_name = parms[parms.index('-n') + 1]
                    elif '--name' in parms:
                        env_name = parms[parms.index('--name') + 1]
                    elif pname is not None:
                        env_name = pname.replace(' ', '_')
                        envs[i] = env.replace('conda create', 'conda create -n '+env_name)

                    if '-y' not in parms:
                        envs[i] = env.replace('conda create', 'conda create -y')
        else:
            print("WARNING: blocked env command: \n{}\nYou may want to run it yourself.".format(env))
            logger.warning('env command is blocked because conda is not avaialbe or in `--freeze` mode: %s', env)


    if type(requirements) is list:
        requirements_pip = " ".join(requirements)
    elif type(requirements) is str:
        requirements_pip = "&& " + requirements
    else:
        raise Exception('wrong requirements type.')

    default_requirements = default_requirements_py2 if is_py2 else default_requirements_py3

    requirements_cmd = "pip install " + " ".join(default_requirements) + ' ' + requirements_pip
    if opt.freeze:
        print("WARNING: blocked pip command: \n{}\nYou may want to run it yourself.".format(requirements_cmd))
        logger.warning('pip command is blocked due to `--freeze` mode: %s', requirements_cmd)
        requirements_cmd = None

    if not opt.freeze and CONDA_AVAILABLE:
        # if env_name is not None:
        requirements_cmd = conda_activate + " "+ env_name + " && " + requirements_cmd
        # if env_name is not None:
        cmd = conda_activate + " " + env_name + " && " + cmd

    secretKey = str(uuid.uuid4())
    abort = threading.Event()
    plugin_info = {'secret': secretKey, 'id': pid, 'abort': abort, 'flags': flags, 'session_id': session_id, 'name': config['name'], 'type': config['type'], 'client_id': client_id, 'signature': plugin_signature}
    addPlugin(plugin_info)

    @sio.on('from_plugin_'+secretKey, namespace=NAME_SPACE)
    async def message_from_plugin(sid, kwargs):
        # print('forwarding message_'+secretKey, kwargs)
        if kwargs['type'] in ['initialized', 'importSuccess', 'importFailure', 'executeSuccess', 'executeFailure']:
            await sio.emit('message_from_plugin_'+secretKey,  kwargs)
            logger.debug('message from %s', pid)
            if kwargs['type'] == 'initialized':
                addPlugin(plugin_info, sid)
        else:
            await sio.emit('message_from_plugin_'+secretKey, {'type': 'message', 'data': kwargs})

    @sio.on('message_to_plugin_'+secretKey, namespace=NAME_SPACE)
    async def message_to_plugin(sid, kwargs):
        # print('forwarding message_to_plugin_'+secretKey, kwargs)
        if kwargs['type'] == 'message':
            await sio.emit('to_plugin_'+secretKey, kwargs['data'])
        logger.debug('message to plugin %s', secretKey)

    try:
        taskThread = threading.Thread(target=launch_plugin, args=[pid, envs, requirements_cmd,
                                      '{} "{}" --id="{}" --host={} --port={} --secret="{}" --namespace={}'.format(cmd, template_script, pid, opt.host, opt.port, secretKey, NAME_SPACE), work_dir, abort, pid, plugin_env])
        taskThread.daemon = True
        taskThread.start()
        return {'success': True, 'initialized': False, 'secret': secretKey, 'work_dir': os.path.abspath(work_dir)}
    except Exception as e:
        logger.error(e)
        return {'success': False}

async def force_kill_timeout(t, obj):
    pid = obj['pid']
    for i in range(int(t*10)):
        if obj['force_kill']:
            await asyncio.sleep(0.1)
        else:
            return
    try:
        logger.warning('Timeout, force quitting %s', pid)
        killPlugin(pid)
    finally:
        return

@sio.on('kill_plugin', namespace=NAME_SPACE)
async def on_kill_plugin(sid, kwargs):
    pid = kwargs['id']
    timeout_kill = None
    if pid in plugins:
        if 'killing' not in plugins[pid]:
            obj = {'force_kill': True, 'pid': pid}
            plugins[pid]['killing'] = True
            def exited(result):
                obj['force_kill'] = False
                logger.info('Plugin %s exited normally.', pid)
                # kill the plugin now
                killPlugin(pid)
            await sio.emit('to_plugin_'+plugins[pid]['secret'], {'type': 'disconnect'}, callback=exited)
            await force_kill_timeout(FORCE_QUIT_TIMEOUT, obj)
    return {'success': True}

@sio.on('register_client', namespace=NAME_SPACE)
async def on_register_client(sid, kwargs):
    global attempt_count
    client_id = kwargs.get('id', str(uuid.uuid4()))
    workspace = kwargs.get('workspace', 'default')
    session_id = kwargs.get('session_id', str(uuid.uuid4()))
    token = kwargs.get('token', None)
    if token != opt.token:
        logger.debug('token mismatch: %s != %s', token, opt.token)
        print('======== Connection Token: '+opt.token + ' ========')
        try:
            webbrowser.open('http://'+opt.host+':'+opt.port+'/about?token='+opt.token, new=0, autoraise=True)
        except Exception as e:
            print('Failed to open the browser.')
        attempt_count += 1
        if attempt_count>= MAX_ATTEMPTS:
            logger.info("Client exited because max attemps exceeded: %s", attempt_count)
            sys.exit(100)
        return {'success': False}
    else:
        attempt_count = 0
        if addClientSession(session_id, client_id, sid):
            confirmation = True
            message = "Another ImJoy session is connected to this Plugin Engine, allow a new session to connect?"
        else:
            confirmation = False
            message = None

        logger.info("register client: %s", kwargs)
        return {'success': True, 'confirmation': confirmation, 'message': message}

def scandir(path, type=None, recursive=False):
    file_list = []
    for f in os.scandir(path):
        if f.name.startswith('.'):
            continue
        if type is None or type == 'file':
            if os.path.isdir(f.path):
                if recursive:
                    file_list.append({'name': f.name, 'type': 'dir', 'children': scandir(f.path, type, recursive)})
                else:
                    file_list.append({'name': f.name, 'type': 'dir'})
            else:
                file_list.append({'name': f.name, 'type': 'file'})
        elif type == 'directory':
            if os.path.isdir(f.path):
                file_list.append({'name': f.name})
    return file_list

@sio.on('list_dir', namespace=NAME_SPACE)
async def on_list_dir(sid, kwargs):
    if sid not in registered_sessions:
        logger.debug('client %s is not registered.', sid)
        return {'success': False, 'error': 'client has not been registered.'}
    path = kwargs.get('path', '~')
    type = kwargs.get('type', None)
    recursive = kwargs.get('recursive', False)
    files_list = {'success': True}
    path = os.path.normpath(os.path.expanduser(path))
    files_list['path'] = path
    files_list['name'] = os.path.basename(os.path.abspath(path))
    files_list['type'] = 'dir'

    files_list['children'] = scandir(files_list['path'], type, recursive)
    return files_list

generatedUrls = {}
generatedUrlFiles = {}
@streamer
async def file_sender(writer, file_path=None):
    """
    This function will read large file chunk by chunk and send it through HTTP
    without reading them into memory
    """
    with open(file_path, 'rb') as f:
        chunk = f.read(2 ** 16)
        while chunk:
            await writer.write(chunk)
            chunk = f.read(2 ** 16)

async def download_file(request):
    # origin = request.headers.get(hdrs.ORIGIN)
    # if origin is None:
    #     # Terminate CORS according to CORS 6.2.1.
    #     raise web.HTTPForbidden(
    #         text="CORS preflight request failed: "
    #              "origin header is not specified in the request")
    urlid = request.match_info['urlid']  # Could be a HUGE file
    if urlid not in generatedUrls:
        raise web.HTTPForbidden(
            text="Invalid URL")
    fileInfo = generatedUrls[urlid]
    name = request.rel_url.query.get('name', None)
    if fileInfo.get('password', False):
        password = request.rel_url.query.get('password', None)
        if password != fileInfo['password']:
            raise web.HTTPForbidden(text="Incorrect password for accessing this file.")
    headers = fileInfo.get('headers', None)
    default_headers = {'Access-Control-Allow-Origin': '*',
                       'Access-Control-Allow-Headers': 'origin',
                       'Access-Control-Allow-Methods': 'GET'
                      }
    if fileInfo['type'] == 'dir':
        dirname = os.path.dirname(name)
        # list the folder
        if dirname == '' or dirname is None:
            if name != fileInfo['name']:
                raise web.HTTPForbidden(text="File name does not match server record!")
            folder_path = fileInfo['path']
            if not os.path.exists(folder_path):
                return web.Response(
                    body='Folder <{folder_path}> does not exist'.format(folder_path=folder_path),
                    status=404
                )
            else:
                file_list = scandir(folder_path, 'file', False)
                headers = headers or {'Content-Disposition': 'inline; filename="{filename}"'.format(filename=name)}
                headers.update(default_headers)
                return web.json_response(file_list, headers=headers)
        # list the subfolder or get a file in the folder
        else:
            file_path = os.path.join(fileInfo['path'], os.sep.join(name.split('/')[1:]))
            if not os.path.exists(file_path):
                return web.Response(
                    body='File <{file_path}> does not exist'.format(file_path=file_path),
                    status=404
                )
            if os.path.isdir(file_path):
                _, folder_name = os.path.split(file_path)
                file_list = scandir(file_path, 'file', False)
                headers = headers or {'Content-Disposition': 'inline; filename="{filename}"'.format(filename=folder_name)}
                headers.update(default_headers)
                return web.json_response(file_list, headers=headers)
            else:
                _, file_name = os.path.split(file_path)
                mime_type = MimeTypes().guess_type(file_name)[0] or 'application/octet-stream'
                headers = headers or {'Content-Disposition': 'inline; filename="{filename}"'.format(filename=file_name), 'Content-Type': mime_type}
                headers.update(default_headers)
                return web.Response(
                    body=file_sender(file_path=file_path),
                    headers= headers
                )
    elif fileInfo['type'] == 'file':
        file_path = fileInfo['path']
        if name != fileInfo['name']:
            raise web.HTTPForbidden(text="File name does not match server record!")
        file_name = fileInfo['name']
        if not os.path.exists(file_path):
            return web.Response(
                body='File <{file_name}> does not exist'.format(file_name=file_path),
                status=404
            )
        mime_type = MimeTypes().guess_type(file_name)[0] or 'application/octet-stream'
        headers = headers or {'Content-Disposition': 'inline; filename="{filename}"'.format(filename=file_name), 'Content-Type': mime_type}
        headers.update(default_headers)
        return web.Response(
            body=file_sender(file_path=file_path),
            headers=headers
        )
    else:
        raise web.HTTPForbidden(text='Unsupported file type: '+ fileInfo['type'])

app.router.add_get('/file/{urlid}', download_file)

@sio.on('get_file_url', namespace=NAME_SPACE)
async def on_get_file_url(sid, kwargs):
    logger.info("generating file url: %s", kwargs)
    if sid not in registered_sessions:
        logger.debug('client %s is not registered.', sid)
        return {'success': False, 'error': 'client has not been registered'}

    path = os.path.abspath(os.path.expanduser(kwargs['path']))
    if not os.path.exists(path):
        return {'success': False, 'error': 'file does not exist.'}
    fileInfo = {'path': path}
    if os.path.isdir(path):
        fileInfo['type'] = 'dir'
    else:
        fileInfo['type'] = 'file'
    if kwargs.get('headers', None):
        fileInfo['headers'] = kwargs['headers']
    _, name = os.path.split(path)
    fileInfo['name'] = name

    if path in generatedUrlFiles:
        return {'success': True, 'url': generatedUrlFiles[path]}
    else:
        urlid = str(uuid.uuid4())
        generatedUrls[urlid] = fileInfo
        generatedUrlFiles[path] = 'http://{}:{}/file/{}?name={}'.format(opt.host, opt.port, urlid, name)
        if kwargs.get('password', None):
            fileInfo['password'] = kwargs['password']
            generatedUrlFiles[path] += ('&password=' + fileInfo['password'])
        return {'success': True, 'url': generatedUrlFiles[path]}


@sio.on('get_file_path', namespace=NAME_SPACE)
async def on_get_file_path(sid, kwargs):
    logger.info("generating file url: %s", kwargs)
    if sid not in registered_sessions:
        logger.debug('client %s is not registered.', sid)
        return {'success': False, 'error': 'client has not been registered'}

    url = kwargs['url']
    urlid = urlparse(url).path.replace('/file/', '')
    if urlid in generatedUrls:
        fileInfo = generatedUrls[urlid]
        return {'success': True, 'path': fileInfo['path']}
    else:
        return {'success': False, 'error': 'url not found.' }

@sio.on('message', namespace=NAME_SPACE)
async def on_message(sid, kwargs):
    logger.info("message recieved: %s", kwargs)

@sio.on('disconnect', namespace=NAME_SPACE)
async def disconnect(sid):
    tasks = disconnectClientSession(sid)
    tasks += disconnectPlugin(sid)
    asyncio.gather(*tasks)
    logger.info('disconnect %s', sid)

def launch_plugin(pid, envs, requirements_cmd, args, work_dir, abort, name, plugin_env):
    if abort.is_set():
        logger.info('plugin aborting...')
        return False
    try:
        if envs is not None and len(envs)>0:
            for env in envs:
                print('Running env command: ' + env)
                logger.info('running env command: %s', env)
                if env not in cmd_history:
                    process = subprocess.Popen(env.split(), shell=False, env=plugin_env, cwd=work_dir)
                    setPluginPID(pid, process.pid)
                    process.wait()
                    cmd_history.append(env)
                else:
                    logger.debug('skip command: %s', env)

                if abort.is_set():
                    logger.info('plugin aborting...')
                    return False

        logger.info('Running requirements command: %s', requirements_cmd)
        print('Running requirements command: ' + requirements_cmd)
        if requirements_cmd is not None and requirements_cmd not in cmd_history:
            process = subprocess.Popen(requirements_cmd, shell=True, env=plugin_env, cwd=work_dir)
            setPluginPID(pid, process.pid)
            ret = process.wait()
            if ret != 0:
                git_cmd = ''
                if shutil.which('git') is None:
                    git_cmd += " git"
                if shutil.which('pip') is None:
                    git_cmd += " pip"
                if git_cmd != '':
                    logger.info('pip command failed, trying to install git and pip...')
                    # try to install git and pip
                    git_cmd = "conda install -y" + git_cmd
                    process = subprocess.Popen(git_cmd.split(), shell=False, env=plugin_env, cwd=work_dir)
                    setPluginPID(pid, process.pid)
                    ret = process.wait()
                    if ret != 0:
                        raise Exception('Failed to install git/pip and dependencies with exit code: '+str(ret))
                    else:
                        process = subprocess.Popen(requirements_cmd, shell=True, env=plugin_env, cwd=work_dir)
                        setPluginPID(pid, process.pid)
                        ret = process.wait()
                        if ret != 0:
                            raise Exception('Failed to install dependencies with exit code: '+str(ret))
                else:
                    raise Exception('Failed to install dependencies with exit code: '+str(ret))
            cmd_history.append(requirements_cmd)
        else:
            logger.debug('skip command: %s', requirements_cmd)
    except Exception as e:
        # await sio.emit('message_from_plugin_'+pid,  {"type": "executeFailure", "error": "failed to install requirements."})
        logger.error('failed to execute plugin: %s', str(e))

    if abort.is_set():
        logger.info('plugin aborting...')
        return False
    # env = os.environ.copy()
    if type(args) is str:
        args = args.split()
    if not args:
        args = []
    # Convert them all to strings
    args = [str(x) for x in args if str(x) != '']
    logger.info('%s task started.', name)
    unrecognized_output = []
    # env['PYTHONPATH'] = os.pathsep.join(
    #     ['.', work_dir, env.get('PYTHONPATH', '')] + sys.path)

    args = ' '.join(args)
    logger.info('Task subprocess args: %s', args)

    # set system/version dependent "start_new_session" analogs
    # https://docs.python.org/2/library/subprocess.html#converting-argument-sequence
    kwargs = {}
    if sys.platform != "win32":
        kwargs.update(preexec_fn=os.setsid)

    process = subprocess.Popen(args, bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              shell=True, env=plugin_env, cwd=work_dir, **kwargs)
    setPluginPID(pid, process.pid)
    # Poll process for new output until finished
    stdfn = sys.stdout.fileno()
    while True:
        out = process.stdout.read(1)
        if out == '' and process.poll() != None:
            break
        os.write(stdfn, out)
        sys.stdout.flush()
        if abort.is_set():
            break
        time.sleep(0)

    try:
        logger.info('Plugin aborting...')
        killProcess(process.pid)
        logger.info('plugin process is killed.')
        output = process.communicate()[0]
        exitCode = process.returncode
    except Exception as e:
        exitCode = 100
    finally:
        if (exitCode == 0):
            return True
        else:
            logger.info('Error occured during terminating a process.\ncommand: %s\n exit code: %s\n', str(args), str(exitCode))
            return False

async def on_startup(app):
    try:
        import pkg_resources  # part of setuptools
        version = pkg_resources.require("imjoy")[0].version
        print('ImJoy Python Plugin Engine (version {})'.format(version))
    except:
        print('ImJoy Plugin Engine is ready.')
        pass
    if opt.serve:
        print('You can access your local ImJoy web app through http://'+opt.host+':'+opt.port+' , imjoy!')
    else:
        print('Please go to https://imjoy.io/#/app with your web browser (Chrome or FireFox)')
    print("Connection Token: " + opt.token)
    sys.stdout.flush()
    # try:
    #     webbrowser.get(using='chrome').open('http://'+opt.host+':'+opt.port+'/#/app?token='+opt.token, new=0, autoraise=True)
    # except Exception as e:
    #     try:
    #         webbrowser.open('http://'+opt.host+':'+opt.port+'/about?token='+opt.token, new=0, autoraise=True)
    #     except Exception as e:
    #         print('Failed to open the browser.')

    # try:
    #     webbrowser.get(using='chrome').open('http://'+opt.host+':'+opt.port+'/about?token='+opt.token, new=0, autoraise=True)
    # except Exception as e:
    #     try:
    #         webbrowser.open('http://'+opt.host+':'+opt.port+'/about?token='+opt.token, new=0, autoraise=True)
    #     except Exception as e:
    #         print('Failed to open the browser.')

# print('======>> Connection Token: '+opt.token + ' <<======')
async def on_shutdown(app):
    print('Shutting down...')
    logger.info('Shutting down the plugin engine...')
    stopped = threading.Event()
    def loop(): # executed in another thread
        for i in range(5):
            print("Exiting: " + str(5 - i), flush=True)
            time.sleep(0.5)
            if stopped.is_set():
                break
        print("Force shutting down now!", flush=True)
        logger.debug('Plugin engine is killed.')
        killProcess(os.getpid())
        # os._exit(1)
    t = threading.Thread(target=loop)
    t.daemon = True # stop if the program exits
    t.start()

    print('Shutting down the plugins...', flush=True)
    killAllPlugins()
    # stopped.set()
    logger.info('Plugin engine exited.')
    # try:
    #     os.remove(pid_file)
    # except Exception as e:
    #     logger.info('Failed to remove the pid file.')

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)
try:
    web.run_app(app, host=opt.host, port=int(opt.port))
except OSError as e:
    if e.errno in {48}:
        print("ERROR: Failed to open port {}, please try to terminate the process which is using that port, or restart your computer.".format(opt.port))
