function validate_demo(results_dir)
% Numerical agreement test only. This is NOT RMS-versus-EMT validation.
if nargin < 1, results_dir = fullfile(fileparts(fileparts(mfilename('fullpath'))),'results'); end
run_case(fullfile(results_dir,'dynamic_input.json'),fullfile(results_dir,'simulink_trace.csv'));
end
