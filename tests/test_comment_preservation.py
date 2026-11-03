import json,tempfile,unittest
from pathlib import Path
from project_core import Workspace
from project_source_guard import bind_baseline
from project_comment_evidence import comments,evidence,clause
from project_acceptance import host_only_requirements,required_host_evidence

class CommentPreservation(unittest.TestCase):
    def test_parser_does_not_accept_string_regex_or_template_as_comments(self):
        source='''// real
const url="https://example.com/*not*/";
const pattern=/[/*]not[/*]/;
const template=`// string ${1 /* expression */}`;
function f(/* argument */) { return 1; /* trailing */ }
/** documentation */
// final
'''
        self.assertEqual(comments(source,'.js'),['// real','/* expression */','/* argument */','/* trailing */','/** documentation */','// final'])
        self.assertEqual(comments('x = "# string" # real\n','.py'),['# real'])
        self.assertEqual(comments('const ui=<div>// text {/* real */}</div>; // end\n','.jsx'),['/* real */','// end'])
        self.assertEqual(comments('const t=`/* text */ ${1 // real\n} // more`;','.js'),['// real'])

    def setup_workspace(self,name,text):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        ws=Workspace(Path(temp.name));ws.write(name,text)
        base=ws.history.checkpoint('original',force=True)
        ws.source_guard_baseline=bind_baseline(ws,base['id'])
        return ws,{'base_version':base['id']}

    def test_actual_receipt_invalidates_cache_and_cannot_be_forged_with_string(self):
        ws,job=self.setup_workspace('app.mjs','// reference\n// reference\nexport const x=1;\n')
        self.assertTrue(evidence(ws,job)['passed'])
        def put(text):ws.write('app.mjs',text,ws.read('app.mjs')['sha256'])
        put('// added\n// reference\n// reference\nexport const x=2;\n')
        proof=evidence(ws,job);self.assertTrue(proof['passed']);self.assertEqual(proof['checked_files'][0]['original_comments'],2)
        put('// reference\nexport const x="// reference";\n')
        self.assertFalse(evidence(ws,job)['passed'])
        put('// reference\n// reference\nexport const x=3;\n')
        self.assertTrue(evidence(ws,json.loads(json.dumps(job)))['passed'])
        ws.source_guard_baseline['sha256']='invalid';self.assertFalse(evidence(ws,job)['passed'])

    def test_reordering_deletion_and_unsupported_changes_fail_closed(self):
        ws,job=self.setup_workspace('app.py','# first\n# second\nx=1\n')
        ws.write('app.py','# second\n# first\nx=2\n',ws.read('app.py')['sha256'])
        self.assertFalse(evidence(ws,job)['passed'])
        ws.path('app.py').unlink();self.assertFalse(evidence(ws,job)['passed'])
        ws,job=self.setup_workspace('app.css','/* first */\n.x {}\n')
        self.assertTrue(evidence(ws,job)['passed'])
        ws.write('app.css','/* first */\n.x {color:red}\n',ws.read('app.css')['sha256'])
        self.assertFalse(evidence(ws,job)['passed'])
        ws,job=self.setup_workspace('settings.jsonc','// preserve\n{"x":1}\n')
        self.assertTrue(evidence(ws,job)['passed'])
        ws.write('settings.jsonc','{"x":2}\n',ws.read('settings.jsonc')['sha256'])
        self.assertFalse(evidence(ws,job)['passed'])
        ws,job=self.setup_workspace('settings.json','{"x":1}\n')
        ws.write('settings.json','{"x":2}\n',ws.read('settings.json')['sha256'])
        self.assertTrue(evidence(ws,job)['passed'])

    def test_mixed_behavior_is_not_automatically_classified_host_only(self):
        text='Preserve the existing reference comments.'
        self.assertTrue(clause(text));self.assertEqual(required_host_evidence(text),['host:source_comments_preserved'])
        self.assertEqual(host_only_requirements([{'id':'R','text':text}]),{'R':['host:source_comments_preserved']})
        self.assertEqual(host_only_requirements([{'id':'R','text':'Preserve the existing comments and add login.'}]),{})

if __name__=='__main__':unittest.main()
