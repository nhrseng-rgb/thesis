function test_workflow()
% Verify Fig.3.1 baseline retry, spacing branches, and research validation gate.
attempts=0;
stages=struct('phase1',@baseline,'phase2',@identity,'phase3',@identity, ...
    'phase4',@identity,'phase5',@validate,'analysis',@identity);
h=run_study(stages,false,3);
assert(attempts==2);
assert(any(contains(h,'NO, retry')));
assert(any(contains(h,'Phase 4 skipped')));
attempts=0;
h=run_study(stages,true,3);
assert(any(strcmp(h,'Phase 4')));
assert(~any(contains(h,'Phase 4 skipped')));
stages.phase5=@identity;
attempts=0; rejected=false;
try
    run_study(stages,false,3);
catch ex
    rejected=contains(ex.message,'validation did not pass');
end
assert(rejected);
disp('PASS: baseline retry, spacing YES/NO, failed validation gate');
    function c=baseline(c)
        attempts=attempts+1;
        c.models_converged=attempts>=2;
        c.baselines_saved=attempts>=2;
    end
    function c=identity(c)
    end
    function c=validate(c)
        c.validation_passed=true;
    end
end
