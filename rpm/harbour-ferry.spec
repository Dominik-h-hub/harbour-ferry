Name:       harbour-ferry

# The qtc5 macros let the Sailfish IDE control qmake/make invocation; the
# fallbacks apply when building outside the IDE (CI, sfdk, rpmbuild).
%{!?qtc_qmake:%define qtc_qmake %qmake}
%{!?qtc_qmake5:%define qtc_qmake5 %qmake5}
%{!?qtc_make:%define qtc_make make}
%{?qtc_builddir:%define _builddir %qtc_builddir}
Summary:    File sync and cloud browser app for Sailfish OS
Version:    0.3
Release:    2
Group:      Qt/Qt
License:    Apache-2.0
URL:        https://github.com/Dominik-h-hub/harbour-ferry
Source0:    %{name}-%{version}.tar.bz2
Requires:   sailfishsilica-qt5 >= 0.10.9
Requires:   pyotherside-qml-plugin-python3-qt5
Requires:   libsailfishapp-launcher

# Harbour allows only a fixed set of dependencies. Both entries below are
# picked up automatically and are not real requirements of this package:
#   /usr/bin/env  from the "#!/usr/bin/env python3" shebangs - no packaged
#                 module is ever executed directly, they all run through
#                 pyotherside or an explicit "python3 <file>" call
#   /bin/sh       from the %%post/%%postun scriptlets below
# python3-base is not listed either: pyotherside-qml-plugin-python3-qt5
# already pulls the interpreter in.
%define __requires_exclude ^(/usr/bin/env|/bin/sh)$
BuildRequires:  pkgconfig(sailfishapp) >= 1.0.2
BuildRequires:  pkgconfig(Qt5Core)
BuildRequires:  pkgconfig(Qt5Qml)
BuildRequires:  pkgconfig(Qt5Quick)
BuildRequires:  qt5-qttools-linguist
BuildRequires:  desktop-file-utils

%description
Ferry is a native file sync client for Sailfish OS using rclone as the
transfer and sync engine. Browse your libraries, upload and download
files, and keep local folders in sync - two-way or upload only, manually
or on a schedule.

%if 0%{?_chum}
Title: Ferry Sync (Cloud File Sync)
Type: desktop-application
DeveloperName: DominikH
Categories:
 - Utility
 - Network
Custom:
  Repo: https://github.com/Dominik-h-hub/harbour-ferry
PackageIcon: https://github.com/Dominik-h-hub/harbour-ferry/raw/main/icons/172x172/harbour-ferry.png
Screenshots:
 - https://github.com/Dominik-h-hub/harbour-ferry/raw/main/docs/images/local-sync.png
 - https://github.com/Dominik-h-hub/harbour-ferry/raw/main/docs/images/remote-sync.png
 - https://github.com/Dominik-h-hub/harbour-ferry/raw/main/docs/images/new-syncpair.png
Links:
  Homepage: https://github.com/Dominik-h-hub/harbour-ferry
  Help: https://forum.sailfishos.org/t/ferry-sync-cloud-file-sync/32278
  Bugtracker: https://github.com/Dominik-h-hub/harbour-ferry/issues
%endif


%prep
%setup -q -n %{name}-%{version}

%build
# Version and Release travel to qmake on the command line: on OBS the source
# tarball produced by tar_git contains no rpm/ directory, so harbour-ferry.pro
# cannot read this file, and the build service rewrites Release anyway.
%qtc_qmake5 APP_VERSION=%{version} APP_RELEASE=%{release}

%qtc_make %{?_smp_mflags}

%install
rm -rf %{buildroot}
%qmake5_install

# Harbour requires every file below /usr/share to be non-executable, and
# the exec bit arrives on its own: the build host's shared folder hands out
# mode 755 for sources that are 644 in git. Normalising here is independent
# of where the package is built. Directories keep their traversal bit.
find %{buildroot}%{_datadir}/%{name} -type f -exec chmod 644 {} +

# ...except rclone, which is executed directly. Its location still violates
# the harbour rules (an ELF binary would have to be /usr/bin/harbour-ferry);
# this package is built for OpenRepos, where that is allowed.
chmod 755 %{buildroot}%{_datadir}/%{name}/bin/rclone

# sync_helper.py needs no exec bit: the systemd unit starts it as an
# argument of /usr/bin/python3, not through its shebang.

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
