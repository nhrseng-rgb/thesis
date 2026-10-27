function data = run_case(input_json, output_csv)
% Called by Python's --backend simulink, or directly from MATLAB.
p = jsondecode(fileread(input_json));
model = build_model(fileparts(mfilename('fullpath')));
mw = get_param(model,'ModelWorkspace');
assignin(mw,'p',p);
save_system(model);
result = sim(model);
f = result.get('frequency');
b = result.get('bess_power');
data = table(f.Time(:),f.Data(:),b.Data(:), ...
    'VariableNames',{'time_s','frequency_hz','bess_mw'});
writetable(data,output_csv);
close_system(model,0);
end
