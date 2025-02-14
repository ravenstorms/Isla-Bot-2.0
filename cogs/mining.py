import discord
import asyncio
from discord.ext import commands
import random
from data.caves import Cave, Drop
from data.equipment import Equipment
from data.user import User
from util.custom_cooldown_mapping import CustomCooldownMapping
import util.dbutil as db
from collections import Counter
from data.blacklist import blacklist
from util.menu import PageMenu, ConfirmationMenu
from datetime import datetime
import pytz


class Mining(commands.Cog):
    def __init__(self, client):
        self.client = client

    monster_failures = Counter()

    class MiningCooldown:
        def __init__(self):
            async def mining_cooldown(message):
                equipment_list = await db.get_equipment_for_user(message.author.id)
                total_stats = User.get_total_stats(message.author.id, equipment_list)
                speed = total_stats['speed']
                cooldown = max(10 - 10 * (speed / 500), 3)
                return commands.Cooldown(1, cooldown, commands.BucketType.user)
            self.mapping = CustomCooldownMapping(mining_cooldown)

        async def __call__(self, ctx: commands.Context):
            bucket = await self.mapping.get_bucket(ctx.message)
            retry_after = bucket.update_rate_limit()
            if retry_after:
                raise commands.CommandOnCooldown(bucket, retry_after)
            return True

    @commands.command(name='mine')
    @commands.check(MiningCooldown())
    async def mine(self, ctx):
        message_embed = discord.Embed(title='Mine!', color=discord.Color.dark_orange())
        if ctx.author.id in blacklist:
            message_embed.description = 'You are blacklisted.'
            await ctx.send(embed=message_embed)
            return
        user = await db.get_user(ctx.author.id)
        cave = Cave.from_cave_name(user['cave'])
        if cave.cave['current_quantity'] == 0:
            message_embed.description = f'{cave.cave["name"]} cannot be mined anymore.'
            await ctx.send(embed=message_embed)
            return
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        total_stats = User.get_total_stats(ctx.author.id, equipment_list, user['blessings'])
        drop_type, drop_value = cave.mine_cave(total_stats['luck'])
        message_embed.description = f'**{ctx.author.mention} mined at {cave.cave["name"]} and found:**\n'
        m = 1  # multiplier
        odds = random.randrange(100)
        if odds <= total_stats['crit'] * 0.25:
            m *= 2
            message_embed.description += 'Critical mine! Extra exp gained.\n'
        if datetime.now(pytz.utc).hour == 23:
            m *= 2
            message_embed.title = 'Happy Hour Mining!'
        if cave.cave['exp'] > 0:
            exp_gained = cave.cave['exp'] + total_stats['exp']
            exp_gained *= m
            exp_gained = int(exp_gained)
            await db.update_user_exp(ctx.author.id, exp_gained)
            message_embed.description += f'`{exp_gained} exp '
            message_embed.description += f'({int(cave.cave["exp"] * m)} + {int(total_stats["exp"] * m)})`'
            message_embed.description += '\n'
        if drop_type == Drop.GOLD:
            gold = drop_value + total_stats['power']
            await db.update_user_gold(ctx.author.id, gold)
            message_embed.description += f'`{gold} gold ({drop_value} + {total_stats["power"]})`\n'
        elif drop_type == Drop.EQUIPMENT:
            equipment_list = await db.get_equipment_for_user(ctx.author.id)
            equipment = User.get_equipment_from_id(equipment_list, drop_value)
            base_equipment = Equipment.get_equipment_from_id(drop_value)
            if equipment:
                if equipment['stars'] < base_equipment['max_stars']:
                    await db.update_equipment_stars(ctx.author.id, drop_value, 1)
                    message_embed.description += f'`{base_equipment["name"]}. Equipment star level increased!`\n'
                else:
                    message_embed.description += f'`{base_equipment["name"]}. Equipment is already at max star level.'
                    message_embed.description += 'Gold recieved instead.`'
                    message_embed.description += f'\n`{base_equipment["value"]} gold`'
                    await db.update_user_gold(ctx.author.id, base_equipment["value"])
            else:
                await db.insert_equipment(ctx.author.id, drop_value, 'inventory')
                message_embed.description += f'`You mined a {base_equipment["name"]}!`\n'
        elif drop_type == Drop.EXP:
            exp_gained = drop_value + total_stats['exp']
            exp_gained *= m
            exp_gained = int(exp_gained)
            await db.update_user_exp(ctx.author.id, exp_gained)
            message_embed.description += f'`{exp_gained} exp ({int(drop_value * m)} + {int(total_stats["exp"] * m)})`\n'
        await ctx.send(embed=message_embed)
        # Monster attack to prevent automation.
        odds = random.randrange(100)
        if odds <= 5:
            emoji_list = ['🤡', '👹', '👽', '👾', '🤖', '👻', '💩']
            action_list = [emoji_list.pop(random.randrange(len(emoji_list))) for i in range(3)]
            correct_action = random.choice(action_list)
            message_embed = discord.Embed(title='Monster!', color=discord.Color.red())
            message_embed.description = f'''
                {ctx.author.mention} has encountered a monster while mining!
                React with {correct_action} within a minute to prevent the
                monster from stealing coin!'''
            message = await ctx.send(embed=message_embed)
            for action in action_list:
                await message.add_reaction(action)

            def check(reaction, user):
                return user.id == ctx.author.id and reaction.message.id == message.id

            try:
                reaction, react_user = await self.client.wait_for('reaction_add', check=check, timeout=60.0)
                if reaction.emoji != correct_action:
                    raise(asyncio.TimeoutError)
            except asyncio.TimeoutError:
                await db.set_user_gold(ctx.author.id, int(user['gold'] * 0.9))
                exp_lost = (user['exp'] - User.level_to_exp(User.exp_to_level(user['exp']))) * 0.1
                await db.update_user_exp(ctx.author.id, -exp_lost)
                message_embed.description = f'''
                    Ouch! You did not react correctly.
                    You lost {int(user["gold"] * 0.9)} gold!
                    You also lost {exp_lost} exp!'''
                await message.edit(embed=message_embed)
                self.monster_failures[ctx.author.id] += 1
                if self.monster_failures[ctx.author.id] >= 5:
                    blacklist[ctx.author.id] = True
            else:
                message_embed.description = 'Whew! You defended yourself against the monster!'
                await message.edit(embed=message_embed)
                self.monster_failures[ctx.author.id] = 0

    @commands.command(name='cave')
    async def cave(self, ctx, *, cave_name=''):
        cave_name = cave_name.title()
        user = await db.get_user(ctx.author.id)
        cave = Cave.from_cave_name(user['cave'])
        cave_quantity = cave.cave['current_quantity']
        user_level = User.exp_to_level(user['exp'])
        if cave_quantity == -1:
            cave_quantity = 'Infinite'
        message_embed = discord.Embed(title='Cave', color=discord.Color.dark_orange())
        to_cave = Cave.from_cave_name(cave_name)
        if cave_name and Cave.verify_cave(cave_name) and user_level >= to_cave.cave['level_requirement']:
            await db.update_user_cave(ctx.author.id, cave_name)
            message_embed.description = f'You have switched to {cave_name}.'
            await ctx.send(embed=message_embed)
        else:
            paginator = commands.Paginator('', '', 1800, '\n')
            paginator.add_line(
                f'''**Current Cave**: `{user["cave"]}`\n
                **Remaining Mines:**`{cave_quantity}`\n
                **__Available Caves:__**\n''')
            for cave in Cave.list_caves_by_level(user_level):
                paginator.add_line(cave)
            menu = PageMenu('Caves', discord.Color.dark_orange(), paginator.pages)
            await menu.start(ctx)

    @commands.command(name='stats')
    async def stats(self, ctx, member: discord.Member = None):
        if not member:
            member = ctx.author
        message_embed = discord.Embed(title=f'{member}\'s Stats', color=discord.Color.dark_teal())
        user = await db.get_user(member.id)
        equipment_list = await db.get_equipment_for_user(member.id)
        user_stats = '\n'.join(
            [f'`{key}`: `{value}`'
                for key, value
                in User.get_total_stats(user, equipment_list, user['blessings']).items()])
        stats = f'''
            `Level: {User.exp_to_level(user["exp"])}` \n
            `{User.get_exp_bar(user["exp"])}`\n
            `Total EXP: {user["exp"]}`\n
            `Gold: {user["gold"]}` \n
            `Blessings: {user["blessings"]}`\n
            **__Stats:__**\n
            {user_stats}
        '''
        message_embed.description = stats
        await ctx.send(embed=message_embed)

    @commands.command(name='equip')
    async def equip(self, ctx, *, equipment_name):
        equipment_name = equipment_name.title()
        message_embed = discord.Embed(title='Equip', color=discord.Color.from_rgb(245, 211, 201))  # peachy color
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        equipment = User.get_equipment_from_name(equipment_list, equipment_name)
        if equipment:
            type = Equipment.get_equipment_from_name(equipment_name)['type'].value
            current_in_location = User.get_equipment_in_location(equipment_list, type)
            if current_in_location:
                await db.update_equipment_location(ctx.author.id, current_in_location['equipment_id'], 'inventory')
            await db.update_equipment_location(ctx.author.id, equipment['equipment_id'], type)
            message_embed.description = f'You have equipped your {equipment_name}'
        else:
            message_embed.description = 'You do not have this equipment!'
        await ctx.send(embed=message_embed)

    @commands.command(name='gear')
    async def gear(self, ctx, *, equipment_name=''):
        equipment_name = equipment_name.title()
        message_embed = discord.Embed(title='Gear', color=discord.Color.from_rgb(245, 211, 201))  # peachy color
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        gear_str = User.get_equipment_stats_str(equipment_list, equipment_name)
        if equipment_name and gear_str:
            message_embed.color = Equipment.lines_to_color[User.get_lines_for_equipment(equipment_list, equipment_name)]
            message_embed.description = gear_str
            file_name = equipment_name.replace(' ', '_') + '.png'
            try:
                image_file = discord.File(f'assets/images/{file_name}', f'{file_name}')
            except:
                file_name = 'Default.png'
                image_file = discord.File(f'assets/images/{file_name}', f'{file_name}')
            message_embed.set_thumbnail(url=f'attachment://{file_name}')
            await ctx.send(file=image_file, embed=message_embed)
        else:
            message_embed.description = '**__Equipped Gear__**:\n' + User.get_equipped_gear_str(equipment_list)
            await ctx.send(embed=message_embed)

    @commands.command(name='inventory')
    async def inventory(self, ctx, *, equipment_name=''):
        equipment_name = equipment_name.title()
        message_embed = discord.Embed(title='Inventory', color=discord.Color.from_rgb(245, 211, 201))  # peachy color
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        equipment_str = User.get_equipment_stats_str(equipment_list, equipment_name)
        if equipment_name and equipment_str:
            message_embed.description = equipment_str
            await ctx.send(embed=message_embed)
        else:
            paginator = commands.Paginator('', '', 1800, '\n')
            for item in User.get_inventory_list(equipment_list):
                paginator.add_line(item)
            menu = PageMenu('Inventory', discord.Color.from_rgb(245, 211, 201), paginator.pages)
            await menu.start(ctx)

    @commands.command(name='leaderboard')
    async def leaderboard(self, ctx):
        user_list = await db.get_top_users_for_exp(50)
        leaderboard_str = ''
        pages = []
        count = 0
        for i in range(len(user_list)):
            leaderboard_str += f'**{i + 1}.** `{self.client.get_user(user_list[i]["user_id"])}` '
            leaderboard_str += f'**Level**: `{User.exp_to_level(user_list[i]["exp"])}` '
            leaderboard_str += f'**EXP:** `{user_list[i]["exp"]}`\n'
            count += 1
            if count >= 10:
                pages.append(leaderboard_str)
                leaderboard_str = ''
                count = 0
        pages.append(leaderboard_str)
        menu = PageMenu('Leaderboard', discord.Color.blue(), pages)
        await menu.start(ctx)

    @commands.command(name='bonus')
    async def bonus(self, ctx, *, equipment_name):
        equipment_name = equipment_name.title()
        user = await db.get_user(ctx.author.id)
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        equipment = User.get_equipment_from_name(equipment_list, equipment_name)
        message_embed = discord.Embed(title='Equipment Bonusing', color=discord.Color.from_rgb(245, 211, 201))
        if equipment:
            message_embed.description = f'Would you like to bonus your {equipment_name} for 1000 gold?'
            result = await ConfirmationMenu(message_embed).prompt(ctx)
            if result:
                if user['gold'] >= 1000:
                    await db.update_user_gold(ctx.author.id, -1000)
                    current_lines = User.get_lines_for_equipment(equipment_list, equipment_name)
                    bonus = Equipment.get_bonus_for_weapon(equipment_name, current_lines)
                    await db.update_equipment_bonus(ctx.author.id, equipment['equipment_id'], bonus)
                    equipment_list = await db.get_equipment_for_user(ctx.author.id)
                    message_embed.description = User.get_equipment_stats_str(equipment_list, equipment_name)
                    message_embed.color = Equipment.lines_to_color[User.get_lines_for_equipment(
                        equipment_list,
                        equipment_name)]
                    file_name = equipment_name.replace(' ', '_') + '.png'
                    try:
                        image_file = discord.File(f'assets/images/{file_name}', f'{file_name}')
                    except:
                        file_name = 'Default.png'
                        image_file = discord.File(f'assets/images/{file_name}', f'{file_name}')
                    message_embed.set_thumbnail(url=f'attachment://{file_name}')
                    await ctx.send(file=image_file, embed=message_embed)
                    return
                else:
                    message_embed.description = 'You do not have enough gold...'
        else:
            message_embed.description = 'You do not own this piece of equipment...'
        await ctx.send(embed=message_embed)

    @commands.command(name='reset')
    async def reset(self, ctx):
        user = await db.get_user(ctx.author.id)
        level = User.exp_to_level(user['exp'])
        blessings = max(int((level - 50) / 5), 0)
        message_embed = discord.Embed(title='Resetting', color=discord.Color.from_rgb(245, 211, 201))
        message_embed.description = f'{ctx.author.mention}, you will recieve {blessings} blessings if you reset exp. '
        message_embed.description += 'You gain 1% exp stat for each blessing you have.'
        result = await ConfirmationMenu(message_embed).prompt(ctx)
        if result:
            await db.set_user_exp(ctx.author.id, 0)
            await db.update_user_blessings(ctx.author.id, blessings)
            await db.update_user_cave(ctx.author.id, 'Beginner Cave')

    @mine.error
    async def mine_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            message_embed = discord.Embed(
                color=discord.Color.dark_orange(),
                title='Mine',
                description=f'You are too tired to mine. {error}')
            await ctx.send(embed=message_embed)
        else:
            print(error)

    @stats.error
    async def stats_error(self, ctx, error):
        if isinstance(error, commands.errors.MemberNotFound):
            message_embed = discord.Embed(
                color=discord.Color.dark_teal(),
                title='Stats',
                description='Cannot find member!')
            await ctx.send(embed=message_embed)
        else:
            print(error)


def setup(client):
    client.add_cog(Mining(client))
