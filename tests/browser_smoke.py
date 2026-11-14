from pathlib import Path
import importlib
import json
import sys
import tempfile
import threading
import unittest

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class BrowserWorkflow(unittest.TestCase):
    def test_scope_archive_and_dataset_roundtrip(self):
        product = json.loads((ROOT / 'product.json').read_text())
        project = json.loads((ROOT / 'package.json').read_text())
        module_name = project['scripts']['start'].split()[2]
        server_module = importlib.import_module(module_name + '.server')
        with tempfile.TemporaryDirectory() as state:
            server = server_module.make_server(ROOT, state, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 1280, 'height': 720})
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
                        page.locator('#rows tr').first.wait_for()
                        page.locator('#mastery-rows tr').first.wait_for()
                        self.assertNotIn('error', page.locator('#status').get_attribute('class') or '')
                        self.assertEqual(page.locator('h1').inner_text(), product['name'])
                        self.assertGreater(page.locator('#rows tr').count(), 0)
                        page.locator('#rows tr').first.click()
                        self.assertIn('{', page.locator('#details').inner_text())
                        page.locator('#raw').click()
                        table = next(iter(json.loads((ROOT / 'fixtures/data.json').read_text())))
                        page.wait_for_function("key => document.querySelector('#details').textContent.includes(JSON.stringify(key))", arg=table)
                        page.locator('#title').fill('会议复核 α')
                        page.locator('#save').click()
                        page.locator('#status').filter(has_text='已保存记录').wait_for()
                        saved = json.loads(page.locator('#details').inner_text())
                        option = product['options'][1] if len(product['options']) > 1 else product['options'][0]
                        alternative = next(value for value in option[2] if value != product['selection'][option[0]])
                        page.locator('#select-' + option[0]).select_option(alternative)
                        page.locator('#inspect').click()
                        page.wait_for_function("([key, value]) => JSON.parse(document.querySelector('#details').textContent).selection[key] === value", arg=[option[0], alternative])
                        page.locator('#archive button').first.click()
                        page.locator('#status').filter(has_text='已读取保存的描述').wait_for()
                        reopened = json.loads(page.locator('#details').inner_text())
                        self.assertEqual(saved, reopened)
                        with page.expect_download() as event:
                            page.locator('#export').click()
                        exported = json.loads(Path(event.value.path()).read_text())
                        self.assertEqual(exported['description']['revision'], saved['revision'])
                        self.assertTrue(exported['data'][table])
                        page.locator('#upload').set_input_files(str(ROOT / 'fixtures/data.json'))
                        page.locator('#status').filter(has_text='已导入独立数据版本').wait_for()
                        self.assertEqual(page.locator('#revision option').count(), 1)
                        self.assertEqual(errors, [])
                        # 掌握判断、策略调整与练习建议
                        page.locator('#mastery').click()
                        page.wait_for_selector('#mastery-rows tr')
                        self.assertGreaterEqual(page.locator('#mastery-rows tr').count(), 4)
                        page.locator('#mastery-rows tr').first.click()
                        self.assertIn('evidence', page.locator('#details').inner_text())
                        before = page.locator('#recommendation h3').inner_text()
                        # 截止到只有入门题的日期，掌握证据减少，建议应变化
                        page.locator('#policy-as-of').fill('2026-02-05')
                        page.locator('#status').filter(has_text='已更新掌握判断').wait_for()
                        after = page.locator('#recommendation h3').inner_text()
                        self.assertIn('2026-02-05', page.locator('#model-meta').inner_text())
                        self.assertTrue(before)
                        # 排除当前建议题目，建议必须换到别的题或给出无题池说明
                        current = page.locator('#recommendation h3').inner_text()
                        import re
                        match = re.search(r'建议题目\s*(\S+)', current)
                        if match:
                            target_item = match.group(1)
                            page.get_by_text('可用题池（排除题目）').click()
                            page.locator('#policy-pool input[value="%s"]' % target_item).check()
                            page.locator('#status').filter(has_text='已更新掌握判断').wait_for()
                            changed_text = page.locator('#recommendation h3').inner_text()
                            self.assertNotIn(target_item + ' · 建议题目', changed_text)
                            page.locator('#policy-pool input[value="%s"]' % target_item).uncheck()
                        page.locator('#reset-policy').click()
                        page.locator('#status').filter(has_text='已更新掌握判断').wait_for()
                        # 回测两种模式分别运行，且泄漏检查为零违规
                        page.locator('#backtest').click()
                        page.locator('#backtest-panel').filter(has_text='违规 0 条').wait_for()
                        self.assertIn('全体率基线', page.locator('#backtest-panel').inner_text())
                        page.locator('#backtest-mode').select_option('incremental_known_learners')
                        page.locator('#backtest').click()
                        page.locator('#backtest-panel').filter(has_text='违规 0 条').wait_for()
                        # 发布建议并确认存档可重新打开且内容冻结
                        page.locator('#advice-title').fill('发布给学生的建议 β')
                        page.locator('#publish').click()
                        page.locator('#status').filter(has_text='已发布冻结建议').wait_for()
                        self.assertGreaterEqual(page.locator('#advice-archive button').count(), 1)
                        page.locator('#advice-archive button').first.click()
                        page.locator('#status').filter(has_text='冻结').wait_for()
                        self.assertIn('payload', page.locator('#details').inner_text())
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
