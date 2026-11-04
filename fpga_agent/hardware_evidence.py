"""Validate physical trial evidence independently of terminal presentation."""
from pathlib import Path
import math

from .evidence import sha


def hardware_valid(project: Path, report: dict) -> tuple[bool,str]:
    try:
        from .tinyllm_hardware import _preflight
        hashes,_,model,tokenizer,crc = _preflight(project)
        if report.get('status') != 'pass' or report.get('bitstream_hashes') != hashes:
            return False,'Hardware acceptance missing, failed, or bound to another bitstream'
        if report.get('model_sha256') != sha(model) or report.get('tokenizer_sha256') != sha(tokenizer):
            return False,'Hardware model/tokenizer evidence changed'
        if report.get('flash_crc32') != f'{crc:08x}' or report.get('jedec_id') != '0020ba18':
            return False,'Hardware Flash identity or full-image CRC not verified'
        if report.get('diagnostic_logits_count') != 1536 or report.get('diagnostic_argmax_tokens') != [403,276,259]:
            return False,'Incomplete board logits verification'
        error=report.get('diagnostic_max_absolute_error')
        if isinstance(error,bool) or not isinstance(error,(int,float)) or not math.isfinite(error) or not 0<=error<=0.002:
            return False,'Invalid board logits error'
        for name in ('tinyllm.py','tinyllm_hardware.py'):
            if report.get('verifier_sha256',{}).get(name) != sha(Path(__file__).with_name(name)):
                return False,'Hardware independent checker changed'
        transcript=project/'reports/hardware-uart.txt'
        if report.get('uart_transcript_sha256') != sha(transcript):
            return False,'Hardware UART transcript missing or changed'
        tokens=report.get('generated_token_ids')
        if not isinstance(tokens,list) or not tokens or any(type(t) is not int or not 0<=t<512 for t in tokens):
            return False,'Missing or invalid board generation tokens'
        return True,'Board JEDEC, full model CRC, 1536 logits and generation sequence matched independent reference'
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,ImportError) as exc:
        return False,str(exc)
