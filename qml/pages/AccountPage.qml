/*
 * Ferry - account dialog (FR-01, FR-01a, FR-02, FR-03).
 * The form is generated from the config_fields definition of the selected
 * backend (AD-09b). Accepting the dialog saves the account and runs the
 * connection test in the background; the result is shown on the settings
 * page via the 'account-result' event.
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

    canAccept: page.valuesRev >= 0
               && !!formValues["url"] && !!formValues["user"]
               && (accountExists || !!formValues["pass"])

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
                onClicked: remorse.execute(qsTr("Removing account"), function() {
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
                        // NOTE (FR-01a): switching backends on an existing
                        // account will require a confirmation + reset once
                        // more than one backend exists (v2).
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
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeExtraSmall
                color: Theme.secondaryHighlightColor
                text: qsTr("Saving runs the connection test in the background - the result appears as a banner.")
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
                    if (summary && summary.url) {
                        page.accountExists = true;
                        values["url"] = summary.url;
                        values["user"] = summary.user;
                        values["use_2fa"] = summary.use_2fa;
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
                    page.formValues = values;
                    page.valuesRev++;
                    page.fields = fieldDefs;
                });
            });
        }

        function saveInBackground() {
            var backendId = page.backends[backendCombo.currentIndex].id;
            // Detached call: the dialog closes now, the result arrives on
            // the settings page via the 'account-result' event.
            call('config_manager.setup_and_test_background',
                 [backendId, page.formValues], function() {});
        }

        function removeAccount() {
            call('config_manager.delete_account', [], function() {
                Notices.show(qsTr("Account removed"));
                pageStack.pop();
            });
        }

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../utilities'));
            importModule('config_manager', function() {
                importModule('backend_manager', function() {
                    call('backend_manager.list_backends', [], function(list) {
                        page.backends = list;
                        if (list.length > 0) {
                            backendCombo.currentIndex = 0;
                            loadFields(list[0].id);
                        }
                    });
                });
            });
        }

        onError: console.log('[ferry] python error: ' + traceback)
    }
}
