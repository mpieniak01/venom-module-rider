import assert from "node:assert/strict";
import {test} from "node:test";
import {diagnosticImageCurrent, decisionView} from "../../web-next/lib/loop-controls";

test("fresh diagnostic image is independently visible but never changes the Pi motion reply",()=>{
  const sha="a".repeat(64),policy={revision:74},detector={sha256:sha};
  const frame={ageMs:100,receivedAt:performance.now(),ageSource:"pi-status-anchor-plus-host-elapsed",decoded:true,stale:false,knownFreshness:true,fileAt:"",id:"source",visionState:"clear",visionPolicy:"",analysis:{preview_only:true,run_id:"native-preview-new-policy",config_revision:74,model_sha256:sha,state:"clear",vision_action:"go",clear_count:3}};
  assert.equal(diagnosticImageCurrent(frame,policy,detector),true);
  for(const analysis of [{...frame.analysis,preview_only:false},{...frame.analysis,config_revision:73},{...frame.analysis,model_sha256:"b".repeat(64)}])
    assert.equal(diagnosticImageCurrent({...frame,analysis},policy,detector),false);
  assert.equal(diagnosticImageCurrent({...frame,ageMs:1001},policy,detector),false);
  assert.equal(diagnosticImageCurrent({...frame,stale:true},policy,detector),false);
  assert.equal(diagnosticImageCurrent({...frame,ageSource:"host-received"},policy,detector),false);
  assert.equal(decisionView({running:false,stale:true,decision:{action:"go",state:"clear",clear_count:3}},detector).reply,"none");
});
