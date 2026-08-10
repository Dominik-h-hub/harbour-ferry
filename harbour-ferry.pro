# Ferry — native Seafile client for Sailfish OS
#
# NOTICE:
# Application name defined in TARGET has a corresponding QML filename.
# If name defined in TARGET is changed, the corresponding QML file, desktop
# file, icon and translation filenames must be changed as well.

TARGET = harbour-ferry

CONFIG += sailfishapp_qml

CONFIG += sailfishapp_i18n
TRANSLATIONS += translations/harbour-ferry-de.ts

# Background sync helper started by the systemd user service.
helper.files = helper
helper.path = /usr/share/$${TARGET}
INSTALLS += helper

# Bundled rclone binary: the matching build for the target
# architecture is picked automatically via QT_ARCH (armv7hl -> arm,
# aarch64 -> arm64, i486 -> i386).
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

# Third-party license notices (rclone is MIT-licensed).
licenses.files = licenses
licenses.path = /usr/share/$${TARGET}
INSTALLS += licenses

# systemd user units for background sync.
systemduser.files = systemd/harbour-ferry-sync.service \
    systemd/harbour-ferry-sync.timer
systemduser.path = /usr/lib/systemd/user
INSTALLS += systemduser

DISTFILES += qml/harbour-ferry.qml \
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
    translations/harbour-ferry-de.ts \
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
    systemd/harbour-ferry-sync.service \
    systemd/harbour-ferry-sync.timer \
    rpm/harbour-ferry.changes.in \
    rpm/harbour-ferry.spec \
    rpm/harbour-ferry.yaml \
    harbour-ferry.desktop
