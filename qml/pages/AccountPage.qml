/*
 * Ferry - account dialog
 * The form is generated from the config_fields definition of the selected
 * backend. Accepting the dialog replaces it on the page stack with
 * the AccountTestPage, which saves the account, runs the connection test and
 * lists the result. Going back from there returns to the page this dialog was
 * opened from.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0
import Sailfish.Silica 1.0
import io.thp.pyotherside 1.5

Dialog {
    id: page

    property var backends: []
    property var fields: []
    property var formValues: ({})
    property int valuesRev: 0
    property bool accountExists: false
    property bool tokenOnly: false
    // An account is stored, but for a different backend than the selected one:
    // saving will replace it and wipe the sync pairs.
    property bool switchesBackend: false

    // What the stored account points at, kept to notice an edit of it.
    property string storedUrl: ""
    property string storedUser: ""

    // Same backend, but the user is pointing it at another server or account.
    // The sync pairs and the stored bisync state belong to the old one, so
    // saving drops them - warn before, the way a backend switch does.
    readonly property bool changesAccount:
        page.accountExists && page.valuesRev >= 0
        && (String(page.formValues["url"] || "") !== page.storedUrl
            || String(page.formValues["user"] || "") !== page.storedUser)

    // Wording of the selected backend (Seafile: libraries, Nextcloud: folders).
    Terminology {
        id: terms
        source: (page.backends.length > 0 && backendCombo.currentIndex >= 0
                 && page.backends[backendCombo.currentIndex].terms)
                ? page.backends[backendCombo.currentIndex].terms : ({})
    }

    canAccept: page.valuesRev >= 0
               && !!formValues["url"] && !!formValues["user"]
               && (accountExists || !!formValues["pass"])

    // Replace (not push): the dialog leaves the stack, so 'back' on the
    // result page lands on the settings/main page again.
    acceptDestination: Qt.resolvedUrl("AccountTestPage.qml")
    acceptDestinationAction: PageStackAction.Replace

    // Silica creates the accept destination before the dialog is accepted, so
    // handing it the form data via acceptDestinationProperties would capture
    // the values while they are still empty. The job is started here instead;
    // the result page only listens for the account-status/-result events.
    onAccepted: python.saveInBackground()

    function setValue(key, value) {
        formValues[key] = value;
        valuesRev++;
    }

    RemorsePopup { id: remorse }

    SilicaFlickable {
        anchors.fill: parent
        contentHeight: column.height

        PullDownMenu {
            MenuItem {
                text: qsTr("Remove account")
                visible: page.accountExists
                onClicked: remorse.execute(qsTr("Removing account and all sync pairs"), function() {
                    python.removeAccount();
                })
            }
        }

        Column {
            id: column
            width: page.width
            spacing: Theme.paddingSmall

            DialogHeader {
                acceptText: qsTr("Save")
                title: qsTr("Account")
            }

            ComboBox {
                id: backendCombo
                label: qsTr("Backend")
                enabled: page.backends.length > 1
                menu: ContextMenu {
                    Repeater {
                        model: page.backends
                        MenuItem { text: modelData.display_name }
                    }
                }
                onCurrentIndexChanged: {
                    if (page.backends.length > 0 && currentIndex >= 0) {
                        // Switching to another backend clears the form (the
                        // stored account belongs to the previous one); saving
                        // then replaces the remote in config_manager.
                        python.loadFields(page.backends[currentIndex].id);
                    }
                }
            }

            Repeater {
                model: page.fields

                delegate: Loader {
                    property var field: modelData
                    width: column.width
                    visible: !field.visible_if
                             || (page.valuesRev >= 0 && !!page.formValues[field.visible_if])
                    sourceComponent: field.type === "switch" ? switchComponent
                                   : (field.secret ? passwordComponent : textComponent)
                }
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.tokenOnly
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryColor
                text: qsTr("With 2FA the server issued a login token; the password itself is not stored. Enter it again only to re-authenticate.")
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.switchesBackend
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.errorColor
                text: qsTr("Switching to another backend replaces the stored account and deletes all sync pairs - they point at the old server.")
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                visible: page.changesAccount
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.errorColor
                text: qsTr("Changing the server or user means another account: all sync pairs and the stored sync state are deleted, because they describe the previous one.")
            }

            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * Theme.horizontalPageMargin
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryHighlightColor
                text: terms.saveHint
            }
        }

        VerticalScrollDecorator { }
    }

    Component {
        id: textComponent
        TextField {
            width: column.width
            label: field.label
            placeholderText: field.placeholder || field.label
            text: page.formValues[field.key] || ""
            inputMethodHints: field.type === "url"
                              ? Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
                              : Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
            onTextChanged: page.setValue(field.key, text)
        }
    }

    Component {
        id: passwordComponent
        PasswordField {
            width: column.width
            label: field.label
            placeholderText: field.label
            text: page.formValues[field.key] || ""
            onTextChanged: page.setValue(field.key, text)
        }
    }

    Component {
        id: switchComponent
        TextSwitch {
            text: field.label
            // Backends may explain a switch (the certificate one warns about
            // what it gives up); TextSwitch hides an empty description.
            description: field.description || ""
            checked: !!page.formValues[field.key]
            onCheckedChanged: page.setValue(field.key, checked)
        }
    }

    Python {
        id: python

        function loadFields(backendId) {
            call('backend_manager.get_fields', [backendId], function(fieldDefs) {
                call('config_manager.get_account_summary', [], function(summary) {
                    var values = {};
                    for (var i = 0; i < fieldDefs.length; i++) {
                        var f = fieldDefs[i];
                        values[f.key] = (f["default"] !== undefined) ? f["default"] : "";
                    }
                    // Only prefill when the stored account belongs to the
                    // selected backend - otherwise this is a fresh setup for
                    // a different service.
                    if (summary && summary.url
                            && summary.backend_id === backendId) {
                        page.accountExists = true;
                        page.switchesBackend = false;
                        // The short server URL, not the stored one: it is
                        // what the user typed, and the backend rebuilds its
                        // technical form when saving.
                        values["url"] = summary.display_url || summary.url;
                        values["user"] = summary.user;
                        // Baseline for the "another account" warning above.
                        page.storedUrl = String(values["url"] || "");
                        page.storedUser = String(values["user"] || "");
                        values["use_2fa"] = summary.use_2fa;
                        // Not stored in the remote but in the app settings -
                        // without this it would read "off" on every edit and
                        // saving would quietly turn the check back on. Only
                        // for a backend that offers the switch: values was
                        // prefilled from fieldDefs above, so the key is the
                        // test for whether this backend has TLS at all.
                        if (values.hasOwnProperty("insecure_tls")) {
                            values["insecure_tls"] = summary.insecure_tls;
                        }
                        call('config_manager.get_account_password', [],
                             function(info) {
                            values["pass"] = info.password;
                            page.tokenOnly = info.token_only;
                            page.formValues = values;
                            page.valuesRev++;
                            page.fields = fieldDefs;
                        });
                        return;
                    }
                    page.accountExists = false;
                    page.tokenOnly = false;
                    page.storedUrl = "";
                    page.storedUser = "";
                    page.switchesBackend = !!(summary && summary.url);
                    page.formValues = values;
                    page.valuesRev++;
                    page.fields = fieldDefs;
                });
            });
        }

        function saveInBackground() {
            if (page.backends.length === 0 || backendCombo.currentIndex < 0) {
                return;
            }
            var backendId = page.backends[backendCombo.currentIndex].id;
            // Detached call: the dialog is gone once this returns, the
            // progress and the result show up on the AccountTestPage.
            call('config_manager.setup_and_test_background',
                 [backendId, page.formValues], function() {});
        }

        function removeAccount() {
            call('config_manager.delete_account', [], function(result) {
                var pairs = (result && result.pairs_removed) || 0;
                Notices.show(pairs > 0
                    ? qsTr("Account removed, %1 sync pair(s) deleted").arg(pairs)
                    : qsTr("Account removed"));
                pageStack.pop();
            });
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            importModule('config_manager', function() {
                importModule('backend_manager', function() {
                    call('backend_manager.list_backends', [], function(list) {
                        page.backends = list;
                        if (list.length === 0) {
                            return;
                        }
                        // Preselect the backend of the stored account so
                        // saving does not rewrite it to another service.
                        call('config_manager.get_account_summary', [],
                             function(summary) {
                            var index = 0;
                            if (summary && summary.backend_id) {
                                for (var i = 0; i < list.length; i++) {
                                    if (list[i].id === summary.backend_id) {
                                        index = i;
                                        break;
                                    }
                                }
                            }
                            if (backendCombo.currentIndex === index) {
                                // No change signal - load the fields here.
                                loadFields(list[index].id);
                            } else {
                                // onCurrentIndexChanged calls loadFields.
                                backendCombo.currentIndex = index;
                            }
                        });
                    });
                });
            });
        }

        onError: console.log('[ferry] python error: ' + traceback)
    }
}
