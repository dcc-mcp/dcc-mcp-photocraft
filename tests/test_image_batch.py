"""Offline tests: bounded native JSON-RPC fake; never launch PhotoCraft."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from types import SimpleNamespace

import pytest

from dcc_mcp_photocraft import image_batch as batch
from dcc_mcp_photocraft.cli_transport import CliError


def png(width=4, height=4, color=6, raw=None):
    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    channels = 4 if color == 6 else 3
    if raw is None:
        raw = (b"\0" + bytes([12, 34, 56, 255][:channels]) * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def opening():
    return {"op": "open_png", "document": "photo", "input": "original.png", "base_layer": "base"}


def operation(op, **kwargs):
    return {"op": op, "document": "photo", **kwargs}


FAKE_HOST = r"""
import base64, copy, json, os, pathlib, sys, time
args=sys.argv
root=pathlib.Path(args[args.index('--automation-write-root')+1])
log=pathlib.Path(os.environ['TEST_NATIVE_LOG'])
mode=os.environ.get('TEST_NATIVE_MODE', '')
state=None
pixels=None
next_id=1
schemas={
 'session_list':{}, 'doc_inspect':{'index':{'type':'integer'}},
 'doc_open':{'path':{'type':'string'}},'doc_close':{'index':{'type':'integer'}},
 'doc_place':{'path':{'type':'string'},'index':{'type':'integer'},'fit':{'type':'boolean'},'scale':{'type':'number'},'center':{'type':'array'}},
 'doc_save':{'path':{'type':'string'},'index':{'type':'integer'},'format':{'type':'string'}},
 'doc_export':{'path':{'type':'string'},'index':{'type':'integer'},'format':{'type':'string'}},
 'command_list':{},'command_run':{'id':{'type':'string'},'params':{'type':'object'},'wait':{'type':'boolean'}}}
commands={
 'layer.setProps':'{"layer":id,"name":str,"visible":bool,"opacity":number}',
 'select.rect':'{"x":int,"y":int,"width":int,"height":int,"ellipse":bool}',
 'layer.layerMask.revealSelection':'{"layer":id}', 'select.deselect':'{}',
 'layer.layerMask.edit':'{"layer":id,"density":number,"feather":number}',
 'layer.layerMask.enabled':'{"layer":id,"enabled":bool}',
 'layer.newAdjustmentLayer.curves':'{"points":json}'}
for line in sys.stdin:
 q=json.loads(line)
 with log.open('a') as f:f.write(json.dumps(q)+'\n')
 if 'id' not in q:continue
 method=q['method']; params=q['params']
 if method=='initialize':
  if mode=='timeout_initialize':time.sleep(10)
  if mode=='oversized':print('x'*9000000,flush=True);continue
  value={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'photocraft','version':'0.15.1'}}
  if mode=='wrong_protocol':value['protocolVersion']='2099-01-01'
  if mode=='missing_protocol':del value['protocolVersion']
  if mode=='missing_identity':del value['serverInfo']
  if mode=='wrong_identity':value['serverInfo']['name']='unrelated-editor'
  if mode=='malformed_identity':value['serverInfo']=[]
  if mode=='missing_version':del value['serverInfo']['version']
  if mode=='malformed_version':value['serverInfo']['version']='future'
  if mode=='numeric_version':value['serverInfo']['version']=15
  if mode=='prerelease_version':value['serverInfo']['version']='0.15.1-dev'
  if mode=='old_version':value['serverInfo']['version']='0.1.9'
  if mode=='oversized_version':value['serverInfo']['version']='9'*5000+'.2.0'
  if mode=='minimum_version':value['serverInfo']['version']='0.2.0'
  if mode=='malformed_capabilities':value['capabilities']=[]
  if mode=='missing_tool_capability':value['capabilities']={}
 elif method=='tools/list':
  tools=[]
  for name,props in schemas.items():
   if mode=='missing_export' and name=='doc_export':continue
   schema={'type':'object','additionalProperties':False,'properties':props}
   if mode=='schema_change' and name=='doc_export':schema['properties']={'unrelated':{'type':'string'}}
   if mode=='schema_ref' and name=='doc_open':schema['$ref']='https://never-contact.invalid/schema'
   tools.append({'name':name,'inputSchema':schema})
  value={'tools':tools}
 else:
  name=params['name']; a=params['arguments']
  if name=='command_list':
   value=[{'id':key,'params':doc} for key,doc in commands.items()
          if not(mode=='missing_command' and key=='layer.setProps')]
  elif name=='session_list':
   value={'documents':[] if state is None else [{'index':0,'width':4,'height':4}],
          'active':None if state is None else 0}
  elif name=='doc_open':
   path=root/a['path']
   if path.suffix=='.png':
    pixels=path.read_bytes()
    state={'width':4,'height':4,'revision':1,'hasSelection':False,'layers':[{'id':next_id,'name':'Background','kind':'Pixel','hasMask':False,'visible':True,'opacity':1.0}]}
    next_id+=1
   else:
    saved=json.loads(path.read_text());state=saved['state'];pixels=base64.b64decode(saved['pixels'])
    for layer in state['layers']:layer['id']+=100
    if mode=='reopen_changed':state['layers'][0]['name']='unexpected'
   value={'index':0,'width':4,'height':4}
  elif name=='doc_close':state=None;value={}
  elif name=='doc_inspect':value=copy.deepcopy(state)
  elif name=='doc_place':
   layer={'id':next_id,'name':'Placed','kind':'Smart Object','hasMask':False,'visible':True,'opacity':1.0}
   next_id+=1;state['layers'].insert(0,layer);value={'layer':layer['id'],'smartObject':True,'bounds':[0,0,4,4]}
  elif name=='command_run':
   command=a['id'];p=a['params']
   if mode=='timeout_command':time.sleep(10)
   if mode=='nested_error':value={'ok':True,'result':{'ok':False,'error':'private /sensitive/path'}}
   elif command=='layer.newAdjustmentLayer.curves':
    layer={'id':next_id,'name':'Curves','kind':'Adjustment','hasMask':True,'visible':True,'opacity':1.0,'adjustment':{'Curves':{'points':p['points']}}}
    next_id+=1;state['layers'].insert(0,layer);value={'layer':layer['id']}
   elif command=='select.rect':state['hasSelection']=True;value={}
   elif command=='select.deselect':state['hasSelection']=False;value={}
   else:
    layer=next(x for x in state['layers'] if x['id']==p['layer'])
    if command=='layer.setProps':layer.update({k:v for k,v in p.items() if k!='layer'})
    elif command=='layer.layerMask.revealSelection':layer['hasMask']=True
    value={'layer':layer['id']}
  elif name=='doc_save':
   (root/a['path']).write_text(json.dumps({'state':state,'pixels':base64.b64encode(pixels).decode()}));value={'path':a['path']}
  elif name=='doc_export':
   target=root/a['path']
   if mode=='output_symlink':target.symlink_to(root/'input-0.png')
   else:target.write_bytes(pixels)
   if mode=='publication_race':pathlib.Path(os.environ['TEST_DESTINATION']).write_bytes(b'concurrent owner')
   value={'path':a['path']}
  else:raise RuntimeError(name)
  value={'content':[{'type':'text','text':json.dumps(value)}],'isError':False}
 response={'jsonrpc':'2.0','id':q['id'],'result':value}
 if mode=='wrong_id':response['id']+=1
 print(json.dumps(response),flush=True)
"""


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    source = tmp_path / "fake_native.py"
    source.write_text(FAKE_HOST)
    log = tmp_path / "requests.jsonl"
    monkeypatch.setenv("TEST_NATIVE_LOG", str(log))
    real_popen = subprocess.Popen
    processes = []
    launches = []

    def launch(args, **kwargs):
        launches.append(args)
        process = real_popen([sys.executable, str(source), *args[1:]], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(batch.subprocess, "Popen", launch)
    read_root, write_root = tmp_path / "read", tmp_path / "write"
    read_root.mkdir()
    write_root.mkdir()
    (read_root / "original.png").write_bytes(png())
    transport = SimpleNamespace(
        binary="official-host", read_root=str(read_root), write_root=str(write_root), timeout_secs=5
    )
    return SimpleNamespace(
        transport=transport,
        read=read_root,
        write=write_root,
        log=log,
        processes=processes,
        launches=launches,
    )


SECURE = pytest.mark.skipif(not batch.secure_publication_supported(), reason="POSIX no-follow publication required")


def requests(fake_host):
    return [json.loads(line) for line in fake_host.log.read_text().splitlines()] if fake_host.log.exists() else []


@pytest.mark.parametrize("mode", ["", "minimum_version"])
def test_native_handshake_accepts_the_official_identity(fake_host, monkeypatch, mode):
    monkeypatch.setenv("TEST_NATIVE_MODE", mode)
    session = batch._NativeSession(fake_host.transport, fake_host.write, 5)
    try:
        session.discover([opening()])
        assert session.native_identity == {
            "name": "photocraft",
            "version": "0.2.0" if mode == "minimum_version" else "0.15.1",
            "protocolVersion": "2025-06-18",
        }
    finally:
        session.close()
    assert any(request["method"] == "tools/list" for request in requests(fake_host))
    assert fake_host.processes[0].poll() is not None


@pytest.mark.parametrize(
    "mode, error",
    [
        ("wrong_protocol", "protocol version"),
        ("missing_protocol", "protocol version"),
        ("missing_identity", "server identity"),
        ("wrong_identity", "server identity"),
        ("malformed_identity", "server identity"),
        ("missing_version", "server version"),
        ("malformed_version", "server version"),
        ("numeric_version", "server version"),
        ("prerelease_version", "server version"),
        ("old_version", "server version"),
        ("oversized_version", "server version"),
        ("malformed_capabilities", "tool capability"),
        ("missing_tool_capability", "tool capability"),
    ],
)
def test_native_handshake_rejects_before_tool_discovery(fake_host, monkeypatch, mode, error):
    monkeypatch.setenv("TEST_NATIVE_MODE", mode)
    session = batch._NativeSession(fake_host.transport, fake_host.write, 5)
    try:
        with pytest.raises(CliError, match=error):
            session.discover([opening()])
    finally:
        session.close()
    assert [request["method"] for request in requests(fake_host)] == ["initialize"]
    assert fake_host.processes[0].poll() is not None


@SECURE
def test_full_owned_workflow_and_rebind(fake_host):
    steps = [
        opening(),
        operation("set_layer", layer="base", name="Renamed original"),
        operation("place_png", input="original.png", layer="placed", center=[2, 2], scale=100),
        operation("mask_rectangle", layer="placed", x=0, y=0, width=4, height=4, ellipse=True),
        operation("set_mask", layer="placed", density=80, feather=1, enabled=False),
        operation("curves", layer="adjustment", points=[[0, 0], [128, 150], [255, 255]]),
        operation("set_layer", layer="adjustment", visible=False),
        operation("save_project", output="editable.pcraft"),
        operation("close"),
        operation("reopen_project", output="editable.pcraft"),
        operation("set_layer", layer="base", opacity=0.5),
        operation("inspect"),
        operation("export_png", output="result.png"),
        operation("close"),
    ]
    original = (fake_host.read / "original.png").read_bytes()
    report = batch.execute_image_batch(fake_host.transport, steps)
    assert report["ok"], report
    assert report["native_mcp"]["version"] == "0.15.1"
    assert report["completed_steps"] == len(steps)
    assert [item["path"] for item in report["outputs"]] == ["editable.pcraft", "result.png"]
    assert report["outputs"][1]["pixels"]["rgba_sha256"] == hashlib.sha256(bytes([12, 34, 56, 255]) * 16).hexdigest()
    assert (fake_host.read / "original.png").read_bytes() == original
    assert len(fake_host.processes) == 1 and fake_host.processes[0].poll() is not None
    assert not list(fake_host.write.glob(".photocraft-batch-*"))
    wire = requests(fake_host)
    layer_changes = [
        q["params"]["arguments"]["params"]
        for q in wire
        if q["method"] == "tools/call"
        and q["params"]["name"] == "command_run"
        and q["params"]["arguments"]["id"] == "layer.setProps"
    ]
    assert layer_changes[-1] == {"layer": 101, "opacity": 0.5}
    launch = fake_host.launches[0]
    assert launch[1] == "mcp"
    assert launch[3] == launch[5]
    assert launch[3] not in {str(fake_host.read), str(fake_host.write)}
    serialized = json.dumps(report)
    assert str(fake_host.write) not in serialized and "input-0.png" not in serialized


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [operation("command_run", id="file.save")],
        [opening(), operation("set_layer", layer="unknown", visible=False)],
        [opening(), operation("open_png", input="original.png", base_layer="base")],
        [opening(), operation("place_png", input="original.png", layer="base", center=[2, 2], scale=100)],
        [opening(), operation("set_layer", layer="base")],
        [opening(), operation("set_layer", layer="base", opacity=float("nan"))],
        [opening(), operation("set_layer", layer="base", opacity=True)],
        [opening(), operation("set_mask", layer="base", enabled=False)],
        [opening(), operation("curves", layer="curve", points=[[0, 0], [100, 20], [100, 30], [255, 255]])],
        [opening(), operation("export_png", output="same.png"), operation("export_png", output="same.png")],
        [operation("reopen_project", output="external.pcraft")],
        [{**opening(), "input": "external.pcraft"}],
        [{**opening(), "input": "../original.png"}],
        [{**opening(), "input": "/tmp/original.png"}],
        [{**opening(), "input": "C:\\original.png"}],
        [{**opening(), "input": "original.png", "index": 0}],
        [opening(), operation("close"), operation("inspect")],
    ],
)
def test_invalid_batches_fail_before_launch(fake_host, steps):
    with pytest.raises(CliError):
        batch.execute_image_batch(fake_host.transport, steps)
    assert not fake_host.processes


@SECURE
@pytest.mark.parametrize("mode", ["missing_export", "schema_change", "schema_ref", "missing_command"])
def test_incompatible_host_rejected_before_open(fake_host, monkeypatch, mode):
    monkeypatch.setenv("TEST_NATIVE_MODE", mode)
    report = batch.execute_image_batch(
        fake_host.transport,
        [opening(), operation("set_layer", layer="base", visible=False), operation("export_png", output="result.png")],
    )
    assert not report["ok"] and not report["mutation_possible"]
    assert not any(q.get("params", {}).get("name") == "doc_open" for q in requests(fake_host))
    assert not list(fake_host.write.iterdir())


@SECURE
def test_nested_error_partial_and_redacted(fake_host, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_MODE", "nested_error")
    report = batch.execute_image_batch(
        fake_host.transport,
        [opening(), operation("set_layer", layer="base", visible=False), operation("export_png", output="result.png")],
    )
    assert report["status"] == "partial" and report["completed_steps"] == 1
    assert report["failed_step"] == 2 and not report["retry_safe"]
    assert "sensitive" not in json.dumps(report)
    assert not report["outputs"] and not list(fake_host.write.iterdir())


@SECURE
def test_timeout_does_not_replay_or_publish(fake_host, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_MODE", "timeout_command")
    fake_host.transport.timeout_secs = 0.25
    started = time.monotonic()
    report = batch.execute_image_batch(
        fake_host.transport, [opening(), operation("set_layer", layer="base", visible=False)]
    )
    assert time.monotonic() - started < 3
    assert report["status"] == "partial" and report["completed_steps"] == 1
    assert "deadline" in report["error"]
    assert len(fake_host.processes) == 1 and fake_host.processes[0].poll() is not None
    assert sum(q.get("params", {}).get("name") == "command_run" for q in requests(fake_host)) == 1


@SECURE
@pytest.mark.parametrize("mode", ["wrong_id", "oversized", "timeout_initialize"])
def test_transport_protocol_failures_are_bounded(fake_host, monkeypatch, mode):
    monkeypatch.setenv("TEST_NATIVE_MODE", mode)
    fake_host.transport.timeout_secs = 0.3
    started = time.monotonic()
    report = batch.execute_image_batch(fake_host.transport, [opening()])
    assert time.monotonic() - started < 3
    assert not report["ok"] and report["completed_steps"] == 0
    assert not report["mutation_possible"]
    assert fake_host.processes[0].poll() is not None


@SECURE
def test_reopen_signature_change_rejected(fake_host, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_MODE", "reopen_changed")
    report = batch.execute_image_batch(
        fake_host.transport,
        [
            opening(),
            operation("save_project", output="own.pcraft"),
            operation("close"),
            operation("reopen_project", output="own.pcraft"),
        ],
    )
    assert not report["ok"] and report["completed_steps"] == 3
    assert "different layer structure" in report["error"]
    assert not report["outputs"]


@SECURE
def test_existing_output_rejected_before_launch(fake_host):
    destination = fake_host.write / "existing.png"
    destination.write_bytes(b"preserve")
    with pytest.raises(CliError, match="already exists"):
        batch.execute_image_batch(fake_host.transport, [opening(), operation("export_png", output="existing.png")])
    assert destination.read_bytes() == b"preserve" and not fake_host.processes


@SECURE
def test_publication_race_never_replaces_destination(fake_host, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_MODE", "publication_race")
    destination = fake_host.write / "result.png"
    monkeypatch.setenv("TEST_DESTINATION", str(destination))
    report = batch.execute_image_batch(fake_host.transport, [opening(), operation("export_png", output="result.png")])
    assert not report["ok"] and not report["outputs"]
    assert destination.read_bytes() == b"concurrent owner"


@SECURE
def test_partial_publication_records_already_published(fake_host, monkeypatch):
    real_link = os.link

    def link(source, target, **kwargs):
        if target == "second.png":
            raise FileExistsError("raced")
        return real_link(source, target, **kwargs)

    # Capability detection uses function identities; replace only after preflight.
    real_publish = batch._publish

    def publish(*args):
        monkeypatch.setattr(batch.os, "link", link)
        return real_publish(*args)

    monkeypatch.setattr(batch, "_publish", publish)
    report = batch.execute_image_batch(
        fake_host.transport,
        [opening(), operation("export_png", output="first.png"), operation("export_png", output="second.png")],
    )
    assert report["status"] == "partial"
    assert [x["path"] for x in report["outputs"]] == ["first.png"]
    assert (fake_host.write / "first.png").exists() and not (fake_host.write / "second.png").exists()


@SECURE
@pytest.mark.parametrize("target", ["input", "input_parent", "read_root", "output_parent", "output"])
def test_symlink_boundaries_fail_closed(fake_host, tmp_path, target):
    if target == "input":
        (fake_host.read / "original.png").unlink()
        (tmp_path / "outside.png").write_bytes(png())
        (fake_host.read / "original.png").symlink_to(tmp_path / "outside.png")
    elif target == "input_parent":
        (fake_host.read / "nested").symlink_to(fake_host.read, target_is_directory=True)
    elif target == "read_root":
        alias = tmp_path / "alias"
        alias.symlink_to(fake_host.read, target_is_directory=True)
        fake_host.transport.read_root = str(alias)
    elif target == "output_parent":
        (fake_host.write / "nested").symlink_to(tmp_path, target_is_directory=True)
    else:
        (fake_host.write / "result.png").symlink_to(tmp_path / "missing")
    first = {**opening(), "input": "nested/original.png"} if target == "input_parent" else opening()
    out = "nested/result.png" if target == "output_parent" else "result.png"
    with pytest.raises(CliError):
        batch.execute_image_batch(fake_host.transport, [first, operation("export_png", output=out)])
    assert not fake_host.processes


@SECURE
def test_fifo_input_does_not_block(fake_host):
    (fake_host.read / "original.png").unlink()
    os.mkfifo(fake_host.read / "original.png")
    started = time.monotonic()
    with pytest.raises(CliError):
        batch.execute_image_batch(fake_host.transport, [opening()])
    assert time.monotonic() - started < 1 and not fake_host.processes


@SECURE
def test_native_output_symlink_is_not_published(fake_host, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_MODE", "output_symlink")
    report = batch.execute_image_batch(fake_host.transport, [opening(), operation("export_png", output="result.png")])
    assert not report["ok"] and not report["outputs"]
    assert not (fake_host.write / "result.png").exists()


def test_platform_without_secure_publication_fails_closed(fake_host, monkeypatch):
    monkeypatch.setattr(batch, "secure_publication_supported", lambda: False)
    with pytest.raises(CliError, match="unavailable on this platform"):
        batch.execute_image_batch(fake_host.transport, [opening()])
    assert not fake_host.processes


def test_independent_root_authorities_required(fake_host):
    fake_host.transport.read_root = None
    with pytest.raises(CliError, match="independent"):
        batch.execute_image_batch(fake_host.transport, [opening()])
    assert not fake_host.processes


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), True])
@SECURE
def test_invalid_duration_fails_before_launch(fake_host, duration):
    fake_host.transport.timeout_secs = duration
    with pytest.raises(CliError, match="timeout"):
        batch.execute_image_batch(fake_host.transport, [opening()])
    assert not fake_host.processes


@SECURE
@pytest.mark.parametrize("bad", [png(width=4097, height=1), png(raw=b"\0" * 10000), b"not a png", png() + b"trailing"])
def test_bad_or_unbounded_png_rejected_before_dispatch(fake_host, bad):
    (fake_host.read / "original.png").write_bytes(bad)
    with pytest.raises(CliError):
        batch.execute_image_batch(fake_host.transport, [opening()])
    assert not fake_host.processes


@SECURE
def test_geometry_checked_before_dispatch(fake_host):
    with pytest.raises(CliError, match="fit inside"):
        batch.execute_image_batch(
            fake_host.transport,
            [opening(), operation("place_png", input="original.png", layer="second", center=[2, 2], scale=200)],
        )
    assert not fake_host.processes


def test_rgb_and_rgba_have_identical_pixel_hash():
    assert batch._png(png(color=2))["rgba_sha256"] == batch._png(png(color=6))["rgba_sha256"]


@pytest.mark.parametrize("kind", [b"acTL", b"tRNS", b"PLTE"])
def test_unsupported_png_chunk_rejected(kind):
    payload = b""
    chunk = struct.pack(">I", 0) + kind + payload + struct.pack(">I", zlib.crc32(kind) & 0xFFFFFFFF)
    data = png()
    with pytest.raises(CliError, match="unsupported"):
        batch._png(data[:-12] + chunk + data[-12:])


@SECURE
def test_cleanup_does_not_remove_replacement_directory(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    stage = parent / "owned"
    stage.mkdir()
    original = stage.stat()
    (stage / "ours").write_text("ours")
    stage.rename(parent / "moved")
    stage.mkdir()
    (stage / "unrelated").write_text("preserve")
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        batch._remove_stage(descriptor, "owned", (original.st_dev, original.st_ino))
    finally:
        os.close(descriptor)
    assert (stage / "unrelated").read_text() == "preserve"
    assert (parent / "moved" / "ours").exists()


def test_expired_deadline_never_dispatches():
    session = batch._NativeSession.__new__(batch._NativeSession)
    session.poisoned = False
    session.deadline = time.monotonic() - 1
    # No process attribute: any attempt to access a pipe would fail this test.
    with pytest.raises(CliError, match="before dispatch"):
        session.rpc("tools/call", {"name": "command_run", "arguments": {}})
    assert session.poisoned


@SECURE
def test_output_budget_stops_before_native_write(fake_host, monkeypatch):
    monkeypatch.setattr(batch, "MAX_TOTAL_OUTPUT_BYTES", 100)
    report = batch.execute_image_batch(fake_host.transport, [opening(), operation("export_png", output="result.png")])
    assert not report["ok"] and report["completed_steps"] == 1
    assert not any(q.get("params", {}).get("name") == "doc_export" for q in requests(fake_host))
    assert not report["outputs"]


def test_native_protocol_fake_is_cross_platform(fake_host, tmp_path):
    session = batch._NativeSession(fake_host.transport, tmp_path, 3)
    try:
        session.discover([opening()])
        assert session.call("session_list", {})["documents"] == []
    finally:
        session.close()
    assert len(fake_host.processes) == 1 and fake_host.processes[0].poll() is not None


def test_native_timeout_cleanup_is_cross_platform(fake_host, tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_MODE", "timeout_initialize")
    session = batch._NativeSession(fake_host.transport, tmp_path, 0.1)
    started = time.monotonic()
    try:
        with pytest.raises(CliError, match="deadline"):
            session.discover([opening()])
    finally:
        session.close()
    assert time.monotonic() - started < 3
    assert len(fake_host.processes) == 1 and fake_host.processes[0].poll() is not None


@pytest.mark.parametrize("kind", [b"iCCP", b"zTXt", b"iTXt", b"eXIf"])
def test_unbounded_png_metadata_never_reaches_native(kind):
    payload = zlib.compress(b"x" * 10000)
    chunk = (
        struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )
    data = png()
    with pytest.raises(CliError, match="metadata"):
        batch._png(data[:-12] + chunk + data[-12:])


@SECURE
def test_replaced_publication_source_cannot_report_verified_success(fake_host, monkeypatch):
    real_link = os.link
    real_publish = batch._publish

    def link(source, target, **kwargs):
        descriptor = kwargs["src_dir_fd"]
        os.unlink(source, dir_fd=descriptor)
        os.symlink("input-0.png", source, dir_fd=descriptor)
        return real_link(source, target, **kwargs)

    def publish(*args):
        monkeypatch.setattr(batch.os, "link", link)
        return real_publish(*args)

    monkeypatch.setattr(batch, "_publish", publish)
    report = batch.execute_image_batch(fake_host.transport, [opening(), operation("export_png", output="result.png")])
    assert report["status"] == "partial" and not report["ok"]
    assert report["outputs"] == [{"path": "result.png", "verified": False}]
    assert "identity changed" in report["error"]


@SECURE
def test_cancellation_cleans_owned_process_and_stage(fake_host, monkeypatch):
    def cancelled(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(batch, "_run_steps", cancelled)
    with pytest.raises(KeyboardInterrupt):
        batch.execute_image_batch(fake_host.transport, [opening()])
    assert len(fake_host.processes) == 1 and fake_host.processes[0].poll() is not None
    assert not list(fake_host.write.iterdir())
