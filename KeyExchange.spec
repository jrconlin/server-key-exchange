%define name python26-keyexchange
%define pythonname KeyExchange
%define version 0.4
%define unmangled_version 0.4
%define unmangled_version 0.4
%define release 1

Summary: Key Exchange server
Name: %{name}
Version: %{version}
Release: %{release}
Source0: %{pythonname}-%{unmangled_version}.tar.gz
License: MPL
Group: Development/Libraries
BuildRoot: %{_tmppath}/%{pythonname}-%{version}-%{release}-buildroot
Prefix: %{_prefix}
BuildArch: noarch
Vendor: Tarek Ziade <tarek@mozilla.com>
Requires: nginx memcached gunicorn python26 python26-memcached python26-setuptools python26-webob python26-paste python26-pastedeploy python26-pastescript python26-services >= 0.2 python26-mako python26-beaker python26-cef
Conflicts: python26-pylibmc

Url: https://hg.mozilla.org/services/server-key-exchange

%description
===================
Key Exchange Server
===================

Implementation of a key exchange server that can be used with protocols like
J-PAKE.

See: https://wiki.mozilla.org/Services/Sync/SyncKey/J-PAKE


%prep
%setup -n %{pythonname}-%{unmangled_version} -n %{pythonname}-%{unmangled_version}

%build
python2.6 setup.py build

%install

# the config files for the app
mkdir -p %{buildroot}%{_sysconfdir}/keyexchange
install -m 0644 etc/keyexchange.conf %{buildroot}%{_sysconfdir}/keyexchange/keyexchange.conf
install -m 0644 etc/production.ini %{buildroot}%{_sysconfdir}/keyexchange/production.ini

# nginx config
mkdir -p %{buildroot}%{_sysconfdir}/nginx
mkdir -p %{buildroot}%{_sysconfdir}/nginx/conf.d
install -m 0644 etc/keyexchange.nginx.conf %{buildroot}%{_sysconfdir}/nginx/conf.d/keyexchange.conf

# logging
mkdir -p %{buildroot}%{_localstatedir}/log
touch %{buildroot}%{_localstatedir}/log/keyexchange.log

# the app
python2.6 setup.py install --single-version-externally-managed --root=$RPM_BUILD_ROOT --record=INSTALLED_FILES

%clean
rm -rf $RPM_BUILD_ROOT

%post
touch %{_localstatedir}/log/keyexchange.log
chown nginx:nginx %{_localstatedir}/log/keyexchange.log
chmod 640 %{_localstatedir}/log/keyexchange.log

%files -f INSTALLED_FILES

%attr(640, nginx, nginx) %ghost %{_localstatedir}/log/keyexchange.log

%dir %{_sysconfdir}/keyexchange/

%config(noreplace) %{_sysconfdir}/keyexchange/*
%config(noreplace) %{_sysconfdir}/nginx/conf.d/keyexchange.conf

%defattr(-,root,root)
