import argparse
import socket
import os
import time
import json
import sys
import subprocess


import discord
from discord.ext import commands

import bot.utils.logger as logger
import bot.utils.events as events
from bot.utils.extensions import load_cog, calculate_lines
from bot.utils.models import Guild
from bot.utils import constants
from bot.utils.api import APIClient


# TODO: Change this when we change to a Docker solution.
__version__ = str(
    subprocess.getoutput("git describe --dirty --tags --dirty --always").split("\n")[0]
)

if os.name == "nt":
    os.system("cls")
if os.name == "posix":
    os.system("clear")


class Kat(commands.Bot):
    LOGGER = logger.get_logger(__name__)

    def __init__(self, **options):
        self.log.info("Discord-py version: " + discord.__version__)
        self.log.info("Kat version: " + __version__)

        # boot start time, used to calculate time taken to boot.
        self.start_time = time.time()

        self.code_line_count = calculate_lines()

        self._is_maintenance_mode = constants.Bot.maintenance_mode

        # New API
        self.session = APIClient()

        self.app_info = None  # gets populated by self.application_info() in on_ready()
        self.id = -1  # quick access to bot's id, populated on_ready()
        self.is_first_boot = 1
        self.is_restart_scheduled = 0
        self._current_presence = None

        self.guild_count = -1
        self.default_prefix = constants.Bot.def_prefix
        self.event_manager = None

        # super call to commands.Bot
        super().__init__(self.get_custom_prefix, **options)

    @property
    def version(self):
        """Return Kats current version"""
        return __version__

    @property
    def log(self):
        """Return the Logger instance"""
        return Kat.LOGGER

    @property
    def current_presence(self):
        """Get the current game presence"""
        return self._current_presence

    @property
    def maintenance_mode(self):
        return self._is_maintenance_mode

    @maintenance_mode.setter
    def maintenance_mode(self, value):
        if value:
            self.log.info("Maintenance mode has been activated.")
            self._is_maintenance_mode = True
            self.dispatch("maintenance_mode", True)
            self.is_restart_scheduled = 1
        elif not value:
            self.log.info("Maintenance mode has been deactivated.")
            self._is_maintenance_mode = False
            self.dispatch("maintenance_mode", False)
            self.is_restart_scheduled = 0

    async def on_error(self, event, *args, **kwargs):
        """Event called when an event raises an exception"""
        self.log.exception("Ignoring exception ", exc_info=sys.exc_info()[2])

    async def on_command_error(self, ctx, exception):
        """Event called when a command raises an exception"""
        # Does the cog have an error handler for this?
        cog = ctx.cog
        if cog:
            if cog._get_overridden_method(cog.cog_command_error) is not None:
                return

        # Does the command have an error handler for this?
        if hasattr(ctx.command, "error"):
            return

        # Get the original exception if not already.
        error = getattr(exception, "original", exception)

        # Any exceptions we want to ignore.
        ignored_exc = (commands.CommandNotFound,)
        if isinstance(error, ignored_exc):
            return

        self.log.error(
            "Ignoring exception from command {} \n{}".format(ctx.command.name, error)
        )

    async def on_connect(self):
        """Call when connected to Discord API"""
        self.log.info("Established connection to Discord API")

    async def on_ready(self):
        """Called when Kat is loaded and connected to Discord API.
        This can be called multiple times when we lose connection to DAPI and other things.
        Don't use this for only when Kat has booted. Ensure you use `first_time_boot` flag
        """
        # PRE-INIT
        self.log.info("Pre-Initialization")

        # INIT
        self.log.info("Initialization")
        self.app_info = await self.application_info()
        self.id = self.app_info.id
        self.log.info(f"Kat is connected to {len(self.guilds)} guilds")
        self.guild_count = len(self.guilds)
        self.setup_events()
        self.load_start_cogs()

        # POST-INIT
        self.log.info(
            " =========== Kat initialized. Took {} seconds =========== ".format(
                format(time.time() - self.start_time, ".2f")
            )
        )
        self.log.info(
            "- Can see {} users, {} guilds ".format(len(self.users), len(self.guilds))
        )

        self.log.info("- Using the following intents:")
        for k, v in self.intents:
            self.log.info("\t\t{}:{}".format(k, v))

        self.is_first_boot = 0

        # self.sql.purge_unguilded_users()
        # for guild in self.guilds:
        #     self.sql.ensure_exists("KatGuild", guild_id=guild.id)

    def setup_events(self):
        if not self.is_first_boot:
            self.log.debug("Not first boot. Skipping event creation.")
            return

        _event_map = constants.EventManager.events
        _event_map = {k: v for x in _event_map for k, v in x.items()}

        self.log.info("Initializing events...")
        self.event_manager = events.EventManager(self)

        self.event_manager.create_events(_event_map)
        self.log.info("Events intialized.")

    async def get_custom_prefix(self, bot, message):
        """Callable, returns the prefix for the message's guild."""
        prefix = (await Guild.get(message.guild.id, self.session)).ensure_setting(
            constants.GuildSettings.prefix, constants.Bot.def_prefix
        )
        return commands.when_mentioned_or(*prefix)(bot, message)

    def load_settings(self):
        """Loads into memory config/config.json"""
        with open("config/config.json", "r") as f:
            self.log.debug("Loading config...")
            return json.load(f)

    def load_start_cogs(self):
        """Goes through all cog names in settings.startup_cogs and attempts to load them."""
        if self.is_first_boot:
            self.log.info("Loading startup cogs...")
            for cog in constants.Bot.startup_cogs:
                try:
                    n, c = load_cog(self, cog)
                except TypeError as e:
                    self.log.exception("Failed to load cog: {}".format(cog), exc_info=e)
            self.log.info("Loaded startup cogs.")

    def initialize(self):
        """Attempts to connect to Discord API and in turn start the bot."""

        _tries = 0
        _disconnected = False
        while _tries != 10 and not _disconnected:
            self.log.info("Attempting to connect to Discord...")
            try:
                self.loop.run_until_complete(self.login(constants.Bot.token))
                self.loop.run_until_complete(self.connect(reconnect=False))
                _disconnected = True
            except (discord.HTTPException, socket.gaierror, Exception) as err:
                self.log.warning(
                    "Failed to connect to Discord. Waiting {} seconds".format(
                        _tries * 5
                    )
                )
                time.sleep(_tries * 5)

                self.log.warning(err)
                _tries += 1


if __name__ == "__main__":

    intents = discord.Intents.default()
    intents.members = True
    intents.presences = True
    intents.typing = True

    kat = Kat(intents=intents)
    args = argparse.ArgumentParser()
    args.add_argument("--orwell", default=False, type=bool)

    _ = args.parse_args()

    if _.orwell:
        kat.is_launched_through_orwell = True

    kat.initialize()
