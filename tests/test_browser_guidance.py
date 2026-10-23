import copy
import json
import unittest
from project_context import inspection_guidance
from project_generation import request_body


class BrowserEvidenceGuidance(unittest.TestCase):
    tools = [{'function': {'name': 'patch_file', 'parameters': {'type': 'object', 'properties': {}, 'required': []}}}]

    def history(self, count=8, last_action=None):
        messages = []
        for index in range(count):
            action = last_action if index == count-1 and last_action else 'fill'
            messages.extend([
                {'role': 'assistant', 'tool_calls': [{'id': str(index), 'function': {
                    'name': 'browser_project', 'arguments': json.dumps({'action': action, 'selector': '#quantity', 'value': str(index)})}}]},
                {'role': 'tool', 'tool_call_id': str(index), 'content': '{"result":{}}'}])
        return messages

    def test_repeated_interactions_request_assertion_without_mutating_evidence(self):
        messages = self.history(); original = copy.deepcopy(messages)
        self.assertIn('8 interactions', inspection_guidance(messages, self.tools))
        self.assertEqual(messages, original)

    def test_real_assertion_or_failed_operation_resets_advice(self):
        self.assertIsNone(inspection_guidance(self.history(last_action='assert'), self.tools))
        messages = self.history(); messages[-1]['content'] = '{"error":"uncertain action"}'
        self.assertIsNone(inspection_guidance(messages, self.tools))

    def test_reopening_does_not_hide_interactions_without_assertions(self):
        for action in ('reload', 'open', 'close'):
            self.assertIn('Browser evidence checkpoint', inspection_guidance(self.history(last_action=action), self.tools))

    def test_short_scenarios_and_read_only_chat_are_unchanged(self):
        self.assertIsNone(inspection_guidance(self.history(7), self.tools))
        self.assertIsNone(inspection_guidance(self.history(), [{'function': {'name': 'read_file'}}]))

    def test_both_inference_protocols_receive_the_same_checkpoint(self):
        for protocol in ('native', 'json'):
            body = request_body([{'role': 'system', 'content': 'test'}, *self.history()], self.tools, 100, protocol=protocol)
            self.assertIn('Browser evidence checkpoint', body['messages'][-1]['content'])
