# Sailfile — native Seafile client for Sailfish OS (M0 walking skeleton).
#
# NOTICE:
# Application name defined in TARGET has a corresponding QML filename.
# If name defined in TARGET is changed, the corresponding QML file, desktop
# file, icon and translation filenames must be changed as well.

TARGET = harbour-sailfile

CONFIG += sailfishapp_qml

# German translation (DEV-03); .qm files are built with lrelease and
# installed to /usr/share/harbour-sailfile/translations.
CONFIG += sailfishapp_i18n
TRANSLATIONS += translations/harbour-sailfile-de.ts

# qml/ (including qml/utilities Python modules), the .desktop file and the
# app icon are deployed automatically by sailfishapp_qml.

# Background sync helper started by the systemd user service (AD-03).
helper.files = helper
helper.path = /usr/share/$${TARGET}
INSTALLS += helper

# Bundled rclone binary (PK-01): the matching build for the target
# architecture is picked automatically via QT_ARCH (armv7hl -> arm,
# aarch64 -> arm64, i486 -> i386). See rclone-binaries/README.md.
equals(QT_ARCH, "arm64") {
    RCLONE_BINARY = $$PWD/rclone-binaries/rclone-aarch64
} else:equals(QT_ARCH, "arm") {
    RCLONE_BINARY = $$PWD/rclone-binaries/rclone-armv7hl
} else {
    RCLONE_BINARY = $$PWD/rclone-binaries/rclone-i486
}
message(Bundling rclone binary for QT_ARCH=$$QT_ARCH: $$RCLONE_BINARY)

rclonebin.path = /usr/share/$${TARGET}/bin
rclonebin.extra = install -D -m 755 $$RCLONE_BINARY $(INSTALL_ROOT)/usr/share/$${TARGET}/bin/rclone
INSTALLS += rclonebin

# Third-party license notices (PK-04: rclone is MIT-licensed).
licenses.files = licenses
licenses.path = /usr/share/$${TARGET}
INSTALLS += licenses

# systemd user units for background sync (PK-02).
systemduser.files = systemd/harbour-sailfile-sync.service \
    systemd/harbour-sailfile-sync.timer
systemduser.path = /usr/lib/systemd/user
INSTALLS += systemduser

DISTFILES += qml/harbour-sailfile.qml \
    qml/cover/CoverPage.qml \
    qml/pages/MainPage.qml \
    qml/pages/SettingsPage.qml \
    qml/pages/AccountPage.qml \
    qml/pages/DiagnosticsPage.qml \
    qml/pages/RemoteBrowserPage.qml \
    qml/pages/FileBrowserPage.qml \
    qml/pages/TextViewerPage.qml \
    qml/pages/TextEditorPage.qml \
    qml/pages/ImageViewerPage.qml \
    qml/pages/SyncPairEditorPage.qml \
    qml/pages/SyncLogPage.qml \
    qml/utilities/file_browser.py \
    qml/utilities/sync_pairs.py \
    qml/utilities/sync_engine.py \
    qml/utilities/settings_manager.py \
    qml/utilities/network.py \
    qml/utilities/notify.py \
    qml/utilities/timer_manager.py \
    qml/utilities/enc_libraries.py \
    translations/harbour-sailfile-de.ts \
    qml/utilities/common.py \
    qml/utilities/credential_store.py \
    qml/utilities/secrets_client.py \
    qml/utilities/backend_manager.py \
    qml/utilities/config_manager.py \
    qml/utilities/remote_browser.py \
    qml/utilities/diagnostics.py \
    qml/utilities/backends/__init__.py \
    qml/utilities/backends/seafile.py \
    helper/sync_helper.py \
    systemd/harbour-sailfile-sync.service \
    systemd/harbour-sailfile-sync.timer \
    rpm/harbour-sailfile.changes.in \
    rpm/harbour-sailfile.spec \
    rpm/harbour-sailfile.yaml \
    harbour-sailfile.desktop
