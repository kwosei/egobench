import json

from egobench.llm.recorded import RecordedLLMClient


def test_recorded_candidate_profiles_differ():
    prompt = "Return CANDIDATE_RESPONSE_JSON with key response.\n<TASK>\nUSER: Explain pytest fixtures.\n</TASK>"
    weak = json.loads(RecordedLLMClient(model="local-weak").complete(prompt).text)["response"]
    detailed = json.loads(RecordedLLMClient(model="local-detailed").complete(prompt).text)["response"]
    assert len(detailed.split()) > len(weak.split())
    assert weak == "I don't know."

