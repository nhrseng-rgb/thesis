function evaluate_research(input_json,output_json)
% Contract bridge for CALIBRATED models. Never substitutes aggregate demo data.
% request.models names validated, project-specific MATLAB functions. Each must
% return measured metrics as documented in SECTION_3_8.md.
request=jsondecode(fileread(input_json));
models=request.models;
if isfield(models,'model_folder'), addpath(models.model_folder); end
if strcmp(request.action,'emt')
    require_function(models,'emt_function');
    result=feval(models.emt_function,request);
    assert(isfield(result,'metrics') && isfield(result,'selected_sites'), ...
        'EMT adapter must return metrics and independently checked selected_sites.');
    result.status='completed';
else
    require_function(models,'rms_function');
    result=feval(models.rms_function,request);
    assert(isfield(result,'metrics') && isstruct(result.metrics),'RMS adapter must return metrics.');
    m=result.metrics;
    frequency_pass=isfield(m,'nadir_hz') && isfield(m,'rocof_hz_s') && isfield(m,'settling_hz') ...
        && m.nadir_hz>=request.thresholds.nadir_hz ...
        && m.rocof_hz_s<=request.thresholds.rocof_hz_s ...
        && m.settling_hz>=request.thresholds.settling_low_hz ...
        && m.settling_hz<=request.thresholds.settling_high_hz;
    result.dynamic_runs=1;
    if frequency_pass
        require_function(models,'fault_function');
        fault=feval(models.fault_function,request);
        assert(isfield(fault,'angle_coi_deviation_deg'),'Fault adapter must return max absolute COI-relative angle deviation in degrees.');
        result.metrics.angle_coi_deviation_deg=fault.angle_coi_deviation_deg;
        result.fault_status='executed'; result.dynamic_runs=2;
    else
        result.fault_status='skipped: frequency constraints failed';
    end
    if ~isfield(result,'physical_limits_verified'), result.physical_limits_verified=false; end
    result.status='completed';
end
file=fopen(output_json,'w'); assert(file~=-1,'Cannot write result JSON');
cleanup=onCleanup(@() fclose(file));
fprintf(file,'%s',jsonencode(result));
end

function require_function(models,name)
assert(isfield(models,name),'Missing calibrated model function: %s',name);
assert(exist(models.(name),'file')==2,'Calibrated function not on MATLAB path: %s',models.(name));
end
