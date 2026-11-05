insert into public.services(tenant_id,name,slug,description,lifecycle,tier,repository_url,metadata)
select id,'Aegis Control Plane','aegis-control-plane','The governed developer productivity and cloud access platform.','production','tier1','https://github.com/apatilgtn/aegis',jsonb_build_object('source','platform_bootstrap') from public.tenants where slug='aegis'
on conflict(tenant_id,slug) do update set description=excluded.description,lifecycle=excluded.lifecycle,tier=excluded.tier,repository_url=excluded.repository_url,metadata=excluded.metadata,updated_at=now();
