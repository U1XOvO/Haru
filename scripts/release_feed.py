"""Publishable appcasts generated only from a complete Ed25519-signed platform matrix."""
import argparse
import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SPARKLE = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
ET.register_namespace('sparkle', SPARKLE)
PLATFORMS = {'windows-x64', 'macos-arm64', 'macos-x64'}


def validate_release_tag(version, channel, tag):
    if channel not in {'stable', 'preview'}:
        raise ValueError('Invalid release channel')
    pattern = re.escape('v' + version) + (r'-preview(?:\.\d+)?' if channel == 'preview' else '')
    if not isinstance(tag, str) or not re.fullmatch(pattern, tag):
        raise ValueError('Release tag does not match version and channel')


def generate(artifacts, output, version, notes):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not re.fullmatch(r'\d+\.\d+\.\d+', version): raise ValueError('Invalid version')
    reports = [json.loads(p.read_text()) for p in Path(artifacts).glob('*.json')]
    if len(reports) != 3 or {r['platform'] for r in reports} != PLATFORMS:
        raise ValueError('A complete Windows/macOS release matrix is required')
    channels={r.get('channel') for r in reports}
    tags={r.get('release_tag') for r in reports}
    if len(channels)!=1 or not channels <= {'stable','preview'} or len(tags)!=1:
        raise ValueError('Mixed release channels or tags')
    channel=channels.pop();tag=tags.pop()
    validate_release_tag(version, channel, tag)
    output = Path(output)/channel;output.mkdir(parents=True,exist_ok=True)
    for report in reports:
        if report['version'] != version or report['updates_enabled'] is not True:
            raise ValueError('Mixed versions or unsigned update payloads cannot be published')
        if report['distribution'] != ('release' if channel=='stable' else 'preview'):
            raise ValueError('Distribution and channel do not match')
        signature = report['signature']
        if len(base64.b64decode(signature,validate=True)) != 64: raise ValueError('Missing Ed25519 signature')
        name = report['artifact']
        if Path(name).name != name or not name.startswith('Haru-' + version + '-'):
            raise ValueError('Invalid artifact name')
        if (Path(artifacts)/name).stat().st_size != report['length']: raise ValueError('Artifact changed after signing')
        Ed25519PublicKey.from_public_bytes(base64.b64decode(report['public_key'],validate=True)).verify(
            base64.b64decode(signature,validate=True), (Path(artifacts)/name).read_bytes())
        rss=ET.Element('rss',version='2.0');rss_channel=ET.SubElement(rss,'channel')
        ET.SubElement(rss_channel,'title').text='Haru updates'
        item=ET.SubElement(rss_channel,'item')
        ET.SubElement(item,'title').text='Haru '+version
        ET.SubElement(item,'description').text=notes
        ET.SubElement(item,'pubDate').text=format_datetime(datetime.now(timezone.utc))
        if report['platform'].startswith('macos'):
            ET.SubElement(item,f'{{{SPARKLE}}}minimumSystemVersion').text='14.0'
        ET.SubElement(item,'enclosure',attrib={
            'url':f'https://github.com/U1XOvO/Haru/releases/download/{tag}/{name}',
            'length':str(report['length']), 'type':'application/octet-stream',
            f'{{{SPARKLE}}}version':version, f'{{{SPARKLE}}}shortVersionString':version,
            f'{{{SPARKLE}}}edSignature':signature,
            f'{{{SPARKLE}}}os':'windows' if report['platform'].startswith('windows') else 'macos',
        })
        ET.ElementTree(rss).write(output/(report['platform']+'.xml'),encoding='utf-8',xml_declaration=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--version',required=True);parser.add_argument('--notes-file',required=True)
    args=parser.parse_args()
    generate(args.artifacts,args.output,args.version,Path(args.notes_file).read_text(encoding='utf-8'))
