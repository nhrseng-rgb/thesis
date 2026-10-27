function history = run_study(stages, run_spacing, max_attempts)
% Research orchestration of Fig.3.1 using user-supplied calibrated stage functions.
% Each stage accepts/returns one context struct. Phase 1 must return
% context.models_converged and context.baselines_saved. Phase 5 must report
% context.validation_passed. This prevents an unvalidated study being completed.
arguments
    stages struct
    run_spacing (1,1) logical = false
    max_attempts (1,1) double {mustBeInteger,mustBePositive} = 3
end
required = {'phase1','phase2','phase3','phase5','analysis'};
if run_spacing, required{end+1} = 'phase4'; end
for i=1:numel(required)
    assert(isfield(stages,required{i}) && isa(stages.(required{i}),'function_handle'), ...
        'Missing stage function: %s',required{i});
end
context = struct(); history = {'START'}; converged = false;
for attempt=1:max_attempts
    context = stages.phase1(context);
    history{end+1} = 'Phase 1';
    converged = isfield(context,'models_converged') && context.models_converged ...
        && isfield(context,'baselines_saved') && context.baselines_saved;
    if converged, break; end
    history{end+1} = 'Baseline gate: NO, retry Phase 1';
end
assert(converged,'Baseline gate failed; Phase 2 was not started.');
history{end+1} = 'Baseline gate: YES';
context = stages.phase2(context); history{end+1} = 'Phase 2';
context = stages.phase3(context); history{end+1} = 'Phase 3';
if run_spacing
    context = stages.phase4(context); history{end+1} = 'Phase 4';
else
    history{end+1} = 'Phase 4 skipped: computing budget decision';
end
context = stages.phase5(context); history{end+1} = 'Phase 5';
context = stages.analysis(context); history{end+1} = 'Data Analysis';
assert(isfield(context,'validation_passed') && context.validation_passed, ...
    'Analysis retained, but research completion blocked: validation did not pass.');
history{end+1} = 'END';
end
