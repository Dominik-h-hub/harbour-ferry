/*
 * Ferry - translated wording for the account form.
 *
 * The form is generated from the backend definition
 * (utilities/backends/*.py -> BACKEND["config_fields"]), and that is Python:
 * lupdate has no Python parser and the package is not in SOURCES, so a
 * string living only there can never reach a .ts file. Every label written
 * that way stayed English in every language - which is what users of the
 * translated app were seeing on the account page.
 *
 * The texts therefore live here, picked by the "text_id" the field carries.
 * The English text stays in the backend definition as the fallback for a
 * text_id this file does not know yet, so a backend added later still shows
 * a usable form - the same split Terminology.qml uses for the
 * library/folder wording.
 *
 * Adding a field to a backend means adding its text_id to both switches
 * below. Nothing breaks without it, the field just stays untranslated.
 *
 * Usage: BackendFields { id: backendFields }
 *        ... label: backendFields.label(field)
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0

QtObject {

    // The label of a form field, as it stands above (or next to) the input.
    function label(field) {
        switch (field ? field.text_id : "") {
        case "server_url":
        case "server_url_nextcloud":
        case "server_url_webdav":
            return qsTr("Server URL");
        case "server_ftp":
            //: Account form, FTP. "ftps://" and "ftp://" are URL schemes and are typed exactly like this - please keep them as they are.
            return qsTr("Server (ftps:// - ftp:// is unencrypted)");
        case "server_sftp":
            //: Account form, SFTP. The field takes either a plain server name or one with a port appended, as in "sftp.example.com:2222".
            return qsTr("Server (host or host:port)");
        case "server_pcloud":
            //: Account form, pCloud. %1 is the server address of pCloud's European region; the field's own default is the American one.
            return qsTr("Server (EU region: %1)").arg(field.text_arg || "");
        case "username":
            return qsTr("Username");
        case "pcloud_email":
            return qsTr("pCloud email address");
        case "password":
            return qsTr("Password");
        case "password_app":
            //: Account form, Nextcloud. An "app password" is Nextcloud's own term for the separate password a server with two-factor authentication issues per application.
            return qsTr("Password or app password (with 2FA)");
        case "use_2fa":
            return qsTr("Two-factor authentication (2FA)");
        case "otp":
            //: Account form, Seafile. OTP: the one-time code from an authenticator app.
            return qsTr("One-time code (OTP)");
        case "insecure_tls":
            return qsTr("Accept self-signed certificates");
        }
        return (field && field.label) ? field.label : "";
    }

    // The explanation shown below a field, empty for a field without one.
    //
    // The long texts are single string literals on purpose, however wide
    // that makes the lines: lupdate does not follow string concatenation
    // inside qsTr(), so splitting them across lines would drop everything
    // after the first piece from the .ts file.
    function description(field) {
        switch (field ? field.text_id : "") {
        case "server_url_nextcloud":
            //: Account form, Nextcloud. The example URL is a technical address - only USERID stands for something the user fills in.
            return qsTr("The server address, or the full WebDAV URL if you know it - Ferry completes a plain server address with the WebDAV path of your account.\nExample: https://cloud.example.com/remote.php/dav/files/USERID");
        case "server_url_webdav":
            //: Account form, plain WebDAV. The two example addresses and the scheme names https/http are technical and stay as they are.
            return qsTr("The full address of the WebDAV share, including the path it is served under. A port only where it is not the default of the scheme (https://dav.example.com:8443/dav).\nWithout a scheme Ferry uses https - put http:// in front of the address for a server without TLS, which sends the password in the clear.");
        case "insecure_tls":
            //: Account form, warning below the certificate switch. Shown for every backend that speaks TLS.
            return qsTr("Only for a server whose certificate no public authority signed. Ferry then accepts any certificate. Use it only on a server you know.");
        }
        return (field && field.description) ? field.description : "";
    }

    // The greyed-out example inside an empty input field.
    //
    // Not translated: a placeholder is an example server address, and there
    // is no language in which "https://cloud.example.com" reads better as
    // something else. A field without one falls back to its label, which is
    // translated.
    function placeholder(field) {
        if (field && field.placeholder) {
            return field.placeholder;
        }
        return label(field);
    }
}
