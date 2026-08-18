# Version scheme (Sailfish OS style): Version-Release, e.g. 0.7-1.
# Keep in sync with the release tag (v<Version>-<Release>-release) and with
# APP_VERSION in qml/utilities/common.py and qml/utilities/diagnostics.py.
#

Name:       harbour-ferry

# The qtc5 macros let the Sailfish IDE control qmake/make invocation; the
# fallbacks apply when building outside the IDE (CI, sfdk, rpmbuild).
%{!?qtc_qmake:%define qtc_qmake %qmake}
%{!?qtc_qmake5:%define qtc_qmake5 %qmake5}
%{!?qtc_make:%define qtc_make make}
%{?qtc_builddir:%define _builddir %qtc_builddir}
Summary:    Native file sync client for Sailfish OS
Version:    0.2
Release:    1
Group:      Qt/Qt
License:    Apache-2.0
URL:        https://openrepos.net
Source0:    %{name}-%{version}.tar.bz2
Requires:   sailfishsilica-qt5 >= 0.10.9
Requires:   pyotherside-qml-plugin-python3-qt5
Requires:   python3-base
Requires:   libsailfishapp-launcher
BuildRequires:  pkgconfig(sailfishapp) >= 1.0.2
BuildRequires:  pkgconfig(Qt5Core)
BuildRequires:  pkgconfig(Qt5Qml)
BuildRequires:  pkgconfig(Qt5Quick)
BuildRequires:  qt5-qttools-linguist
BuildRequires:  desktop-file-utils

%description
Ferry is a native file sync client for Sailfish OS using rclone as the
transfer and sync engine. Browse your libraries, upload and download
files, and keep local folders synchronized bidirectionally - manually or
on a schedule.


%prep
%setup -q -n %{name}-%{version}

%build
%qtc_qmake5

%qtc_make %{?_smp_mflags}

%install
rm -rf %{buildroot}
%qmake5_install

# rclone is installed with mode 755 by the qmake install rule (arch-specific
# binary picked automatically, see rclone-binaries/README.md).
chmod 755 %{buildroot}%{_datadir}/%{name}/helper/sync_helper.py

desktop-file-install --delete-original       \
  --dir %{buildroot}%{_datadir}/applications             \
   %{buildroot}%{_datadir}/applications/*.desktop

%post
# Reload user systemd so replaced unit files take effect immediately
# (otherwise the old unit definition stays loaded until next login).
systemctl-user daemon-reload || :

%postun
systemctl-user daemon-reload || :

%files
%defattr(-,root,root,-)
%{_datadir}/%{name}
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/*/apps/%{name}.png
/usr/lib/systemd/user/%{name}-sync.service
/usr/lib/systemd/user/%{name}-sync.timer
