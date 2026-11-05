import io
import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from fpga_agent.claude_gateway import ClaudeGateway, normalize_messages


class GatewayTests(unittest.TestCase):
    def test_system_normalization_preserves_order_messages_tools_and_string(self):
        payload={'system':[{'type':'text','text':'first','cache_control':{'type':'ephemeral'}},
                           {'type':'text','text':'second'}],
                 'messages':[{'role':'user','content':'goal'}],'tools':[{'name':'Read'}]}
        normalized=normalize_messages(payload)
        self.assertEqual(normalized['system'],'first\n\nsecond')
        self.assertEqual(normalized['messages'],payload['messages'])
        self.assertEqual(normalized['tools'],payload['tools'])
        self.assertIsInstance(payload['system'],list)
        self.assertEqual(normalize_messages({'system':'already text'}),{'system':'already text'})
        native={'system':[{'type':'text','text':'instructions'}],
                'messages':[{'role':'user','content':'goal'},{'role':'system','content':[{'type':'text','text':'environment'}]}]}
        self.assertEqual(normalize_messages(native),{'system':'instructions\n\nenvironment',
                                                    'messages':[{'role':'user','content':'goal'}]})
        with self.assertRaises(ValueError):
            normalize_messages({'system':[{'type':'image','source':{}}]})

    def test_authenticated_loopback_forwards_stream_query_and_upstream_auth_then_closes(self):
        received=[]
        body=b'event: message_start\ndata: {"type":"message_start"}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n'
        class Response(io.BytesIO):
            code=200
            headers={'Content-Type':'text/event-stream'}
        class Upstream:
            def open(self,request,timeout):
                received.append(request)
                return Response(body)
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        gateway=ClaudeGateway('https://gateway.example/v1','fixture-upstream-key',{})
        with patch('fpga_agent.claude_gateway.urllib.request.build_opener',return_value=Upstream()):
            env=gateway.start()
        thread=gateway.thread
        try:
            url=env['ANTHROPIC_BASE_URL']+'/v1/messages?beta=true'
            data=json.dumps({'model':'fixture','system':[{'type':'text','text':'one'},{'type':'text','text':'two'}],
                             'messages':[{'role':'user','content':'goal'}],'stream':True}).encode()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                client.open(urllib.request.Request(url,data=data),timeout=3)
            self.assertEqual(caught.exception.code,401)
            self.assertEqual(received,[])
            request=urllib.request.Request(url,data=data,headers={'Authorization':'Bearer '+env['ANTHROPIC_AUTH_TOKEN']})
            with client.open(request,timeout=3) as response:
                self.assertEqual(response.read(),body)
                self.assertEqual(response.headers['Content-Type'],'text/event-stream')
            self.assertEqual(received[0].full_url,'https://gateway.example/v1/messages?beta=true')
            self.assertEqual(received[0].get_header('Authorization'),'Bearer fixture-upstream-key')
            self.assertEqual(json.loads(received[0].data)['system'],'one\n\ntwo')
        finally:
            gateway.close()
        self.assertFalse(thread.is_alive())


if __name__=='__main__':
    unittest.main()
