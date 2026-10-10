/*
 *  The contents of this file are subject to the Initial Developer's Public
 *  License Version 1.0 (the "License"); you may not use this file except in
 *  compliance with the License. You may obtain a copy of the License at
 *  http://www.ibphoenix.com/main.nfs?a=ibphoenix&page=ibp_idpl.
 *
 *  Software distributed under the License is distributed AS IS, WITHOUT
 *  WARRANTY OF ANY KIND, either express or implied.
 *
 *  Copyright (c) 2026 Firebird Project and all contributors signed below.
 *  All Rights Reserved.
 */

#include "firebird.h"
#include "Authentication.h"
#include "../common/Auth.h"
#include "../common/classes/GetPlugins.h"
#include "../common/classes/ParsedList.h"
#include "../common/db_alias.h"
#include "../common/isc_f_proto.h"
#include "../common/os/path_utils.h"
#include "../common/status.h"
#include "../common/unicode_util.h"
#include "../common/utils_proto.h"

#include <cstring>
#include <new>
#include <vector>

namespace Firebird::WireProtocol
{
namespace
{

constexpr unsigned MAX_ALIAS = 255;
constexpr unsigned MAX_USER = 255;
constexpr unsigned MAX_SRP_LOGIN = 63;
constexpr unsigned MAX_PASSWORD = 4096;
constexpr unsigned MAX_AUTH_DATA = 65536;
constexpr unsigned MAX_AUTH_ROUNDS = 16;
constexpr const char* AUTH_PLUGIN = "Srp256";

void checkStopping(const std::atomic<bool>& stopping)
{
	if (stopping.load())
		Arg::Gds(isc_att_shutdown).raise();
}

unsigned checkedLength(const char* text, unsigned maximum, ISC_STATUS error)
{
	if (!text)
		Arg::Gds(error).raise();
	unsigned length = 0;
	while (length <= maximum && text[length])
		++length;
	if (length > maximum)
		Arg::Gds(error).raise();
	if (!UnicodeUtil::utf8WellFormed(length, reinterpret_cast<const UCHAR*>(text), nullptr))
		Arg::Gds(isc_malformed_string).raise();
	return length;
}

void checkAlias(const char* alias)
{
	const unsigned length = checkedLength(alias, MAX_ALIAS, isc_unavailable);
	if (!length || !std::strcmp(alias, ".") || !std::strcmp(alias, ".."))
		Arg::Gds(isc_unavailable).raise();
	for (unsigned i = 0; i < length; ++i)
	{
		const unsigned char c = alias[i];
		if (c <= ' ' || c == 127 || c == ':' || c == '/' || c == '\\')
			Arg::Gds(isc_unavailable).raise();
	}
}

void requireLocalFile(const PathName& file)
{
	// Remote's provider can also recognize mounted remote files. Check mounts,
	// not only explicit host/path syntax, before entering authentication plugins.
	if (file.isEmpty() || PathUtils::isRelative(file) || ISC_check_if_remote(file, true))
		Arg::Gds(isc_unavailable).raise();
}

bool permitsSrp(const Config* config, unsigned type)
{
	const ParsedList plugins(config->getPlugins(type));
	for (const auto& name : plugins)
	{
		if (name == AUTH_PLUGIN)
			return true;
	}
	return false;
}

// An in-memory exchange has no native wire cipher. Neither block retains the
// derived session key supplied by Srp256. This is not transport encryption.
class DiscardCryptKey final : public AutoIface<ICryptKeyImpl<DiscardCryptKey, CheckStatusWrapper>>
{
public:
	void setSymmetric(CheckStatusWrapper*, const char*, unsigned, const void*) override {}
	void setAsymmetric(CheckStatusWrapper*, const char*, unsigned, const void*, unsigned, const void*) override {}
	const void* getEncryptKey(unsigned* length) override { *length = 0; return nullptr; }
	const void* getDecryptKey(unsigned* length) override { *length = 0; return nullptr; }
};

void copyAuthData(CheckStatusWrapper* status, UCharBuffer& target, unsigned length, const void* data)
{
	if (length > MAX_AUTH_DATA || (length && !data))
	{
		Arg::Gds(isc_auth_data).copyTo(status);
		return;
	}
	UCHAR* to = target.getBuffer(length);
	if (length)
		std::memcpy(to, data, length);
}

class ServerBlock;

class ClientBlock final : public RefCntIface<IClientBlockImpl<ClientBlock, CheckStatusWrapper>>
{
public:
	ClientBlock(const string& login, unsigned length, const char* input)
		: login(getPool(), login), password(length + 1, 0),
		  fromClient(getPool()), fromServer(getPool())
	{
		if (length)
			std::memcpy(password.data(), input, length);
	}

	const char* getLogin() override { return login.c_str(); }
	const char* getPassword() override { return password.data(); }
	const UCHAR* getData(unsigned* length) override
	{
		*length = fromServer.getCount();
		return fromServer.begin();
	}
	void putData(CheckStatusWrapper* status, unsigned length, const void* data) override
	{
		copyAuthData(status, fromClient, length, data);
	}
	ICryptKey* newKey(CheckStatusWrapper*) override { return &key; }
	IAuthBlock* getAuthBlock(CheckStatusWrapper*) override { return nullptr; }

private:
	~ClientBlock() override
	{
		// Volatile writes prevent dead-store elimination before vector deallocation.
		volatile char* bytes = password.data();
		for (unsigned i = 0; i < password.size(); ++i)
			bytes[i] = 0;
	}

	const string login;
	std::vector<char> password;
	UCharBuffer fromClient, fromServer;
	DiscardCryptKey key;
	friend class ServerBlock;
};

class ServerBlock final : public AutoIface<IServerBlockImpl<ServerBlock, CheckStatusWrapper>>
{
public:
	explicit ServerBlock(ClientBlock& client) : client(client) {}
	const char* getLogin() override { return client.getLogin(); }
	const UCHAR* getData(unsigned* length) override
	{
		*length = client.fromClient.getCount();
		return client.fromClient.begin();
	}
	void putData(CheckStatusWrapper* status, unsigned length, const void* data) override
	{
		copyAuthData(status, client.fromServer, length, data);
	}
	ICryptKey* newKey(CheckStatusWrapper*) override { return &key; }

private:
	ClientBlock& client;
	DiscardCryptKey key;
};

void authenticate(const Config* config, const string& login, unsigned passwordLength,
	const char* password, const std::atomic<bool>& stopping, ClumpletWriter& dpb)
{
	if (!permitsSrp(config, IPluginManager::TYPE_AUTH_SERVER) ||
		!permitsSrp(config, IPluginManager::TYPE_AUTH_CLIENT))
	{
		(Arg::Gds(isc_login_error) << Arg::Gds(isc_vld_plugins)).raise();
	}

	RefPtr<ClientBlock> clientBlock(FB_NEW ClientBlock(login, passwordLength, password));
	ServerBlock serverBlock(*clientBlock);
	// Plugins are destroyed before the blocks they receive. Do not use the EDS
	// wrapper here: it requires an existing engine attachment/thread context.
	GetPlugins<IClient> client(IPluginManager::TYPE_AUTH_CLIENT, config, AUTH_PLUGIN);
	GetPlugins<IServer> server(IPluginManager::TYPE_AUTH_SERVER, config, AUTH_PLUGIN);
	if (!client.hasData() || !server.hasData())
		(Arg::Gds(isc_login_error) << Arg::Gds(isc_vld_plugins)).raise();
	Auth::WriterImplementation writer;
	writer.setPlugin(AUTH_PLUGIN);

	for (unsigned round = 0; round < MAX_AUTH_ROUNDS; ++round)
	{
		checkStopping(stopping);
		FbLocalStatus status;
		const int clientResult = client.plugin()->authenticate(&status, clientBlock);
		checkStopping(stopping);
		if (status->getState() & IStatus::STATE_ERRORS)
			(Arg::Gds(isc_login_error) << Arg::StatusVector(&status)).raise();
		if (clientResult != IAuth::AUTH_SUCCESS && clientResult != IAuth::AUTH_MORE_DATA)
			Arg::Gds(isc_login).raise();

		checkStopping(stopping);
		const int serverResult = server.plugin()->authenticate(&status, &serverBlock, &writer);
		checkStopping(stopping);
		if (status->getState() & IStatus::STATE_ERRORS)
			(Arg::Gds(isc_login_error) << Arg::StatusVector(&status)).raise();
		if (serverResult == IAuth::AUTH_SUCCESS && clientResult == IAuth::AUTH_SUCCESS)
		{
			writer.store(&dpb, isc_dpb_auth_block);
			if (!dpb.find(isc_dpb_auth_block) || !dpb.getClumpLength())
				Arg::Gds(isc_login_error).raise();
			return;
		}
		if (serverResult != IAuth::AUTH_MORE_DATA || clientResult == IAuth::AUTH_SUCCESS)
			Arg::Gds(isc_login).raise();
	}
	Arg::Gds(isc_login_error).raise();
}

void addPeer(ClumpletWriter& dpb, const char* address, unsigned port, const char* protocolName)
{
	string endpoint;
	endpoint.printf("%s/%u", address, port);
	ClumpletWriter record(ClumpletReader::UnTagged, MAX_UCHAR - 2);
	record.insertString(isc_dpb_addr_protocol, std::strchr(address, ':') ? "TCPv6" : "TCPv4");
	record.insertString(isc_dpb_addr_endpoint, endpoint);
	ClumpletWriter path(ClumpletReader::UnTagged, MAX_UCHAR - 2);
	path.insertBytes(isc_dpb_address, record.getBuffer(), record.getBufferLength());
	dpb.insertBytes(isc_dpb_address_path, path.getBuffer(), path.getBufferLength());
	dpb.insertString(isc_dpb_remote_protocol, protocolName);
}

} // namespace

IAttachment* authenticateAndAttach(CheckStatusWrapper* status,
	const char* databaseAlias, const char* user, unsigned passwordLength, const char* password,
	const char* peerAddress, unsigned peerPort, const char* protocolName,
	const char* applicationName, const std::atomic<bool>& stopping)
{
	try
	{
		status->init();
		checkStopping(stopping);
		checkAlias(databaseAlias);
		checkedLength(user, MAX_USER, isc_login);
		if (passwordLength > MAX_PASSWORD || (passwordLength && !password) ||
			(passwordLength && std::memchr(password, 0, passwordLength)))
		{
			Arg::Gds(isc_login).raise();
		}
		if (passwordLength && !UnicodeUtil::utf8WellFormed(passwordLength,
			reinterpret_cast<const UCHAR*>(password), nullptr))
		{
			Arg::Gds(isc_malformed_string).raise();
		}
		if (!checkedLength(peerAddress, 64, isc_login_error) || !peerPort || peerPort > 65535 ||
			!checkedLength(protocolName, 63, isc_login_error))
		{
			Arg::Gds(isc_login_error).raise();
		}
		if (applicationName)
			checkedLength(applicationName, 255, isc_login_error);

		string login(user);
		fb_utils::dpbItemUpper(login);
		if (login.isEmpty() || login.length() > MAX_SRP_LOGIN)
			Arg::Gds(isc_login).raise();

		PathName alias(databaseAlias), file;
		ISC_utf8ToSystem(alias);
		RefPtr<const Config> config;
		// expandDatabaseName loads and locks databases.conf. resolveAlias alone
		// does neither; accept only its explicitly aliased result, never a path.
		if (!expandDatabaseName(alias, file, &config))
			Arg::Gds(isc_unavailable).raise();
		requireLocalFile(file);
		if (!config->getRemoteAccess())
			(Arg::Gds(isc_no_priv) << "remote" << "database" << databaseAlias).raise();

		const char* securityDatabase = config->getSecurityDatabase();
		if (!securityDatabase || !securityDatabase[0])
			Arg::Gds(isc_secdb_name).raise();
		PathName securityFile;
		RefPtr<const Config> securityConfig;
		expandDatabaseName(securityDatabase, securityFile, &securityConfig);
		requireLocalFile(securityFile);
		// SrpServer removes Loopback from the security database's provider list.
		// Reject an unsupported profile before its getNonLoopbackProviders() can
		// encounter an empty list, and require the local engine we rely on here.
		bool hasEngine = false;
		const ParsedList securityProviders(securityConfig->getPlugins(IPluginManager::TYPE_PROVIDER));
		for (const auto& provider : securityProviders)
		{
			if (provider == CURRENT_ENGINE)
			{
				hasEngine = true;
				break;
			}
		}
		if (!hasEngine)
			Arg::Gds(isc_unavailable).raise();
		checkStopping(stopping);

		// Construct the DPB from trusted fields only. No raw password/username,
		// client provider config, trusted role, or internal-attachment tag enters it.
		ClumpletWriter dpb(ClumpletReader::dpbList, MAX_DPB_SIZE);
		authenticate(config, login, passwordLength, password, stopping, dpb);
		dpb.insertTag(isc_dpb_utf8_filename);
		dpb.insertString(isc_dpb_lc_ctype, "UTF8");
		dpb.insertInt(isc_dpb_sql_dialect, SQL_DIALECT_V6);
		dpb.insertString(isc_dpb_config, EMBEDDED_PROVIDERS);
		addPeer(dpb, peerAddress, peerPort, protocolName);
		if (applicationName && applicationName[0])
			dpb.insertString(isc_dpb_process_name, applicationName);

		checkStopping(stopping);
		DispatcherPtr provider;
		return provider->attachDatabase(status, databaseAlias, dpb.getBufferLength(), dpb.getBuffer());
	}
	catch (const Exception& ex)
	{
		ex.stuffException(status);
	}
	catch (const std::bad_alloc&)
	{
		Arg::Gds(isc_virmemexh).copyTo(status);
	}
	return nullptr;
}

} // namespace Firebird::WireProtocol
