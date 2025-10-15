Summary: Job-level Monitoring Client
Name: hpcperfstats
Version: 2.4
Release: 1%{?dist}
License: GPL
Vendor: Texas Advanced Computing Center
Group: System Environment/Base
Packager: TACC - sharrell@tacc.utexas.edu
Source: hpcperfstats-%{version}.tar.gz
BuildRequires: systemd libev

%{?systemd_requires}

%description
This package provides the hpcperfstatsd daemon, \
along with a systemd script to provide control.

%prep
%setup

%build
./configure
make
sed -i 's/CONFIGFILE/\%{_sysconfdir}\/hpcperfstats\/hpcperfstats.conf/' src/hpcperfstats.service
sed -i 's/localhost/stats.frontera.tacc.utexas.edu/' src/hpcperfstats.conf
sed -i 's/default/frontera/' src/hpcperfstats.conf

%install
mkdir -p  %{buildroot}/%{_sbindir}/
mkdir -p  %{buildroot}/%{_sysconfdir}/hpcperfstats/
mkdir -p  %{buildroot}/%{_unitdir}/
install -m 744 src/hpcperfstatsd %{buildroot}/%{_sbindir}/hpcperfstatsd
install -m 644 src/hpcperfstats.conf %{buildroot}/%{_sysconfdir}/hpcperfstats/hpcperfstats.conf
install -m 644 src/hpcperfstats.service %{buildroot}/%{_unitdir}/hpcperfstats.service

%files
%{_sbindir}/hpcperfstatsd
%{_sysconfdir}/hpcperfstats/hpcperfstats.conf
%{_unitdir}/hpcperfstats.service
%dir %{_sysconfdir}/hpcperfstats

%post
%systemd_post hpcperfstats.service

%preun
%systemd_preun hpcperfstats.service

%postun
%systemd_postun_with_restart hpcperfstats.service
