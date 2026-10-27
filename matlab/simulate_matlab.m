function data = simulate_matlab(input_json, output_csv)
% MATLAB-only equation reference when Simulink components are not installed.
% Not a substitute for validating the generated Simulink model.
p = jsondecode(fileread(input_json));
t = (0:p.dt:p.stop_s)';
after = t(t>=p.event_s);
opts = odeset('RelTol',1e-8,'AbsTol',1e-10,'MaxStep',0.04);
[~,x] = ode45(@rhs,after,[0;0;0],opts);
x = [zeros(sum(t<p.event_s),3); x];
data = table(t,60+x(:,1),p.load_mw*x(:,3), ...
    'VariableNames',{'time_s','frequency_hz','bess_mw'});
writetable(data,output_csv);
    function dx = rhs(time,state)
        df=state(1); pg=state(2); pb=state(3);
        c=p.capacity_mw/p.load_mw;
        M=2*(p.h_s+p.virtual_h_s*c)/60;
        loss=(time>=p.event_s)*p.loss_mw/p.load_mw;
        command=min(c,max(-c,-p.bess_gain*c*df));
        dx=[(pg+pb-loss-p.damping*df)/M; ...
            (-p.governor_gain*df-pg)/p.governor_tau; ...
            (command-pb)/p.bess_tau];
    end
end
