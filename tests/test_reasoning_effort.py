import unittest
from types import SimpleNamespace

from daemon.models import reasoning
from daemon.routers.anthropic_proxy import _effort_of
from daemon.routers.openai_proxy import _apply_effort


def recipe(style):
    return SimpleNamespace(reasoning=style)


class NearestLevelTests(unittest.TestCase):
    def test_a_word_the_template_has_is_kept(self):
        self.assertEqual(reasoning.nearest("qwen3.8", "medium"), "medium")

    def test_high_on_qwen38_goes_up_to_xhigh(self):
        # Equally far from medium and xhigh: the larger wins.
        self.assertEqual(reasoning.nearest("qwen3.8", "high"), "xhigh")

    def test_max_on_a_switch_model_is_thinking_on(self):
        self.assertEqual(reasoning.nearest("switch", "max"), "high")

    def test_none_on_a_model_that_always_thinks_is_its_least(self):
        self.assertEqual(reasoning.nearest("gpt-oss", "none"), "low")

    def test_aliases_and_case(self):
        self.assertEqual(reasoning.nearest("qwen3.8", "OFF"), "none")

    def test_not_an_effort(self):
        self.assertIsNone(reasoning.nearest("qwen3.8", "turbo"))


class ApplyEffortTests(unittest.TestCase):
    def test_qwen38_effort_goes_into_the_template_kwargs(self):
        body = {"model": "m", "reasoning_effort": "high"}
        self.assertTrue(_apply_effort(body, recipe("qwen3.8")))
        self.assertEqual(body["chat_template_kwargs"], {"reasoning_effort": "xhigh"})
        self.assertNotIn("reasoning_effort", body)

    def test_none_switches_thinking_off_and_keeps_other_kwargs(self):
        body = {"reasoning_effort": "none", "chat_template_kwargs": {"keep": 1}}
        _apply_effort(body, recipe("switch"))
        self.assertEqual(body["chat_template_kwargs"], {"keep": 1, "enable_thinking": False})

    def test_enable_thinking_false_reaches_a_budget_template(self):
        body = {"chat_template_kwargs": {"enable_thinking": False}}
        _apply_effort(body, recipe("seed-oss"))
        self.assertEqual(body["chat_template_kwargs"]["thinking_budget"], 0)

    def test_gpt_oss_keeps_a_top_level_effort(self):
        body = {"reasoning_effort": "xhigh"}
        _apply_effort(body, recipe("gpt-oss"))
        self.assertEqual(body["reasoning_effort"], "high")

    def test_responses_style_effort(self):
        body = {"reasoning": {"effort": "low", "summary": "auto"}}
        _apply_effort(body, recipe("nemotron-low"))
        self.assertEqual(body["chat_template_kwargs"], {"low_effort": True})
        self.assertNotIn("reasoning", body)

    def test_deepseek_v41_switches_thinking_off_its_own_way(self):
        body = {"reasoning_effort": "none"}
        _apply_effort(body, recipe("deepseek-v4.1"))
        self.assertEqual(body["chat_template_kwargs"], {"thinking": False})

    def test_nothing_asked_nothing_changed(self):
        body = {"messages": []}
        self.assertFalse(_apply_effort(body, recipe("qwen3.8")))
        self.assertEqual(body, {"messages": []})

    def test_a_model_without_a_style_is_left_alone(self):
        body = {"reasoning_effort": "high"}
        self.assertFalse(_apply_effort(body, recipe("")))
        self.assertEqual(body, {"reasoning_effort": "high"})


class AnthropicEffortTests(unittest.TestCase):
    def test_effort_setting_wins(self):
        self.assertEqual(_effort_of({"output_config": {"effort": "max"},
                                     "thinking": {"type": "disabled"}}), "max")

    def test_thinking_disabled(self):
        self.assertEqual(_effort_of({"thinking": {"type": "disabled"}}), "none")

    def test_budget_sizes(self):
        self.assertEqual(_effort_of({"thinking": {"type": "enabled", "budget_tokens": 2048}}), "low")
        self.assertEqual(_effort_of({"thinking": {"type": "enabled", "budget_tokens": 10000}}), "medium")
        self.assertEqual(_effort_of({"thinking": {"type": "enabled", "budget_tokens": 31999}}), "high")

    def test_nothing_asked(self):
        self.assertIsNone(_effort_of({"thinking": {"type": "adaptive"}}))


class StylesTests(unittest.TestCase):
    def test_levels_follow_the_scale_and_default_is_one_of_them(self):
        for name, (default, levels) in reasoning.STYLES.items():
            order = [reasoning.SCALE.index(lvl) for lvl in levels]
            self.assertEqual(order, sorted(order), name)
            self.assertIn(default, levels, name)

