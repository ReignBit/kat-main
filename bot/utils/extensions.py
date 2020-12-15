"""Utility methods for loading and unload extensions"""
import gzip
import json
import sys
import os
import traceback
import random
import inspect
import importlib
import inspect
import pkgutil
from typing import Iterator

from string import Template
import asyncio
import discord
from discord.ext import commands
from discord.ext.commands import errors, Cog


from bot.utils import logger, events, database
from bot.utils.models import KatGuild, KatMember, KatUser


# TODO: Think about fragmenting this class.
class KatCog(commands.Cog):
    """discord.Cog extension for Kat support."""
    def __init__(self, bot):
        self.bot = bot

        self.log = logger.get_logger(self.qualified_name)

        self.sql = database.SqlEngine()
        self.sql.create_sql_session()

        # Load GLOBAL settings from config/
        self.load_settings()

        # Response Handling.
        self.responses = {}
        self.load_responses()

        # New EventManager stuff
        self.event_manager = events.EventManager(self.bot, cog=self)

        for cmd in self.walk_commands():
            self.log.info("Registered command %s" % cmd.qualified_name)

    def load_settings(self):
        try:
            self.settings = self.bot.settings['cogs'][self.qualified_name.lower(
            )]
        except KeyError:
            self.settings = {}

    def _fallback_setting(self, key):
        """Fetches fallback setting in case self.bot.settings returns KeyError"""
        # TODO: do this.
        pass

    def get_guild_setting(self, guild_id: discord.Guild, setting_key, default=None):
        """
            Attempt to retrieve a guild setting (setting_key) from the DB
            If the guild has no key for setting_key, then return default
        """
        self.log.debug("fetching guild_setting")
        guild_settings = self.sql.ensure_exists(
            "KatGuild", guild_id=guild_id).settings
        try:
            guild_settings = json.loads(guild_settings)
        except json.JSONDecodeError:
            return default

        _path = setting_key.split(".")
        _result = guild_settings
        for x in _path:
            _result = _result.get(x, {})
            self.log.debug(_result)
            if _result == {}:
                self.log.warning(
                    "Key {} doesn't exist in {}".format(x, guild_settings))
        return _result

    def get_guild_all_settings(self, guild_id):
        """
            Mostly for verbosity. Returns the JSON dict for a guild's settings
        """
        guild = self.sql.ensure_exists("KatGuild", guild_id=guild_id)
        try:
            guild_settings = json.loads(guild.settings)
        except json.JSONDecodeError:
            return guild, {}
        return guild, guild_settings

    def set_guild_setting(self, guild_id: discord.Guild, setting_key, value):
        guild, guild_json = self.get_guild_all_settings(guild_id)
        self._nested_set(guild_json, setting_key.split('.'), value)
        self.log.debug(guild_json)

        jsonified = json.dumps(guild_json)

        guild.settings = jsonified
        self.bot.sql_session.commit()

    def _nested_set(self, dic, keys, value):
        for key in keys[:-1]:
            dic = dic.setdefault(key, {})
        dic[keys[-1]] = value

    def ensure_guild_setting(self, guild_id: discord.Guild, setting_key, default):
        """
            Checks if a guild_setting of setting_key exists. If not it creates the key with the value of default
        """
        _ = self.get_guild_setting(guild_id, setting_key)
        if _ == None:
            # guild setting doesnt exist
            self.set_guild_setting(guild_id, setting_key, default)

    # Response stuff

    def load_responses(self):
        # Load responses
        self.responses['common'] = read_resource(
            "/languages/english/common.json")
        self.log.info("Loaded responses for common")
        try:
            # Try to load any cog-specific responses
            # TODO: Per-guild languages
            self.responses[self.qualified_name.lower()] = \
                read_resource(
                    "/languages/english/{}.json".format(self.qualified_name.lower()))

            self.log.info("Loaded responses for {}".format(
                self.qualified_name))
        except (FileNotFoundError, IOError):
            self.log.warning(
                "Failed to load Cog-Specific language file for `{}`".format(self.qualified_name))

    def get_response(self, response, **args):
        _path = response.split(".")
        _result = self.responses
        for x in _path:
            _result = _result.get(x, {})
            if _result == {}:
                raise KeyError(
                    "Key {} doesn't exist in {}".format(x, response))
        choice = random.choice(_result).format(**args, cog=self, bot=self.bot)
        return choice

    def get_embed(self, embed, **kwargs) -> dict:
        """Returns the embed JSON for embed, along with formatted args"""
        _path = embed.split(".")
        _result = self.responses
        for x in _path:
            _result = _result.get(x, {})
            if _result == {}:
                raise KeyError(
                    "Key {} doesn't exist in {}".format(x, embed))

        json_string = json.dumps(_result)

        t = Template(json_string)
        t = t.substitute(**kwargs)
        self.log.debug(t)
        _result = json.loads(t)
        return _result

    async def throw_command_error_to_message(self, ctx, error):
        exc_type, _, exc_traceback = sys.exc_info()
        self.log.warning(
            f"{ctx.command} encountered an error: {error} : {exc_type} {exc_traceback}")
        embed = discord.Embed(color=discord.Color.red())
        embed.set_author(name="Command Failed",
                         icon_url="https://cdn.discordapp.com/emojis/669531431428685824.png?v=1")
        embed.description = "```py\n{}\n```".format(
            traceback.format_exc(limit=2))
        await ctx.send(embed=embed)

    async def cog_command_error(self, ctx, error):

        if isinstance(error, commands.MissingPermissions):
            await ctx.channel.send(self.get_response("common.error.missing_permissions", cmd=ctx.command))
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.channel.send(self.get_response("common.error.missing_args", args=error.param.name))
            return

        if isinstance(error, commands.MemberNotFound):
            await ctx.channel.send(self.get_response("common.error.member_not_found", error=error.argument))
            return

        try:
            self.log.exception(error.original)
        except:
            pass
        self.log.warn("error: " + str(error))
        self.log.warn(str(type(error)))
        await ctx.channel.send(self.get_response('common.error.command_error'))

    async def cog_before_invoke(self, ctx):
        self.log.info(
            f"[USER {ctx.author.name} | {ctx.author.id}] [GUILD {ctx.guild.name} | {ctx.guild.id}] Performed {ctx.command}")

    # async def cog_check(self, ctx) -> bool:
    #     """Only allow speficied roles to invoke the commands in this cog."""
    #     guild = self.sql.ensure_exists("KatGuild", guild_id=ctx.guild.id)
    #     # guild setting generation.
    #     roles = guild.ensure_setting("roles", {'moderators': [
    #                                  '111111111111111111'], 'administrators': ['administrator', '111111111111111111']})

    #     except commands.errors.MissingAnyRole:
    #         await ctx.send(self.get_response('common.error.permission_error'))
    #         return False

    #     except Exception as err:
    #         self.log.warn(err)

    def cog_unload(self):
        self.log.info(f"Unloading {self.qualified_name}")
        self.run = False
        self.event_manager.destroy()
        self.sql.destroy()
        self.log.destroy()
        del self


def unqualify(name: str) -> str:
    """Return an unqualified name given a qualified module/package `name`."""
    return name.rsplit(".", maxsplit=1)[-1]


def walk_extensions() -> Iterator[str]:
    """Yield extension names from the cogs subpackage."""
    # Avoid circular import.
    from bot import cogs
    def on_error(name: str):
        raise ImportError(name=name)

    for module in pkgutil.walk_packages(cogs.__path__, f"{cogs.__name__}.", onerror=on_error):
        if unqualify(module.name).startswith("_"):
            # Ignore module/package names starting with an underscore.
            continue

        if module.ispkg:
            imported = importlib.import_module(module.name)
            if not inspect.isfunction(getattr(imported, "setup", None)):
                # If it lacks a setup function, it's not an extension.
                continue

        yield module.name


def load_cog(bot, cog_name) -> Cog:
    """Attempts to load a cog from 'cogs/'"""
    try:
        matches = get_cog_name_matches(cog_name)
        for cog in matches:
            bot.load_extension(cog)
            bot.log.info("Loaded %s." % cog_name)
            return cog_name, bot.get_cog(cog_name)

        raise errors.ExtensionNotFound

    except errors.ExtensionError as err:
        bot.log.warn("Failed to load Cog: %s" % cog_name)
        bot.log.warn("re-raising exception: %s" % err)
        raise err

def get_cog_name_matches(cog_name):
    matches = []
    for extension in walk_extensions():
        if cog_name in extension:
            matches.append(extension)
    return matches

def unload_cog(bot, cog_name) -> Cog:
    """Attempts to unload a cog from 'cogs/'"""

    try:
        # Find extension
        matches = get_cog_name_matches(cog_name)
        if matches > 1:
            raise errors.ExtensionNotFound(f"Ambiguous extension name. Choose between the following:\n{matches}")

        old_cog = bot.get_cog(matches[0])
        bot.unload_extension(matches[0])
        bot.log.info("Unloaded %s" % cog_name)
        return cog_name, old_cog

    except errors.ExtensionError as err:
        bot.log.crit("Failed to unload Cog: %s" % cog_name)
        bot.log.crit("re-raising exception: %s" % err)
        raise err


def compress_file(path):
    """Takes a file and compresses it using g-zip, returns bytes"""
    with open(path, "rb") as f:
        compressed = gzip.compress(f.read())
    return compressed

def read_resource(filepath: str):
    """Read data from a resource file filepath located in bot/resources/"""
    if os.path.exists("bot/resources/" + filepath):
        with open("bot/resources/" + filepath, "r", encoding="utf-8") as f:
            return json.load(f)

def write_resource(filepath: str, data):
    """Write data to a resource file filepath located in bot/resources/"""
    with open("bot/resources/" + filepath, "w") as f:
        f.write(data)