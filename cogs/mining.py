import discord
import asyncio
from discord.ext import commands
import random
from data.caves import Cave, Drop
from data.equipment import Equipment
from data.user import User
from util.custom_cooldown_mapping import CustomCooldownMapping
import util.dbutil as db

class Mining(commands.Cog):
    def __init__(self, client):
        self.client = client

    class MiningCooldown:
        def __init__(self):
            async def mining_cooldown(message):
                user = await db.get_user(message.author.id)
                equipment_list = await db.get_equipment_for_user(message.author.id)
                total_stats = User.get_total_stats(message.author.id, equipment_list)
                speed = min(total_stats['speed'], 95)
                cooldown = 10 - 10 * (speed / 100)
                return commands.Cooldown(1, cooldown, commands.BucketType.user)
            self.mapping = CustomCooldownMapping(mining_cooldown)

        async def __call__(self, ctx: commands.Context):
            bucket = await self.mapping.get_bucket(ctx.message)
            retry_after = bucket.update_rate_limit()
            if retry_after:
                raise commands.CommandOnCooldown(bucket, retry_after)
            return True

    @commands.command(name = 'mine')
    @commands.check(MiningCooldown())
    async def mine(self, ctx):
        message_embed = discord.Embed(title = 'Mine!', color=discord.Color.dark_orange())
        user = await db.get_user(ctx.author.id)
        cave = Cave.from_cave_name(user['cave'])
        if cave.cave['current_quantity'] == 0:
            message_embed.description = f'{cave.cave["name"]} cannot be mined anymore.'
            await ctx.send(embed = message_embed)
            return
        drop_type, drop_value = cave.mine_cave()
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        total_stats = User.get_total_stats(ctx.author.id, equipment_list)
        message_embed.description = f'**{ctx.author.mention} mined at {cave.cave["name"]} and found:**\n'
        exp_gained = cave.cave['exp'] + int(cave.cave['exp'] * total_stats['exp'] / 100)
        await db.update_user_exp(ctx.author.id, exp_gained)
        message_embed.description += f'`{exp_gained} exp ({cave.cave["exp"]} + {int(cave.cave["exp"] * total_stats["exp"] / 100)})`\n'
        if drop_type == Drop.GOLD:
            gold = drop_value + total_stats['power']
            await db.update_user_gold(ctx.author.id, gold)
            message_embed.description += f'`{gold} gold ({drop_value} + {total_stats["power"]})`\n'
        elif drop_type == Drop.EQUIPMENT:
            equipment_list = await db.get_equipment_for_user(ctx.author.id)
            equipment = User.get_equipment_from_id(equipment_list, drop_value)
            base_equipment = Equipment.get_equipment_from_id(drop_value)
            if equipment:
                if equipment['stars'] <= base_equipment['max_stars']:
                    await db.update_equipment_stars(ctx.author.id, drop_value, 1)
                    message_embed.description += f'`{base_equipment["name"]}. Equipment star level increased!`\n'
                else:
                    message_embed.description += f'`{base_equipment["name"]}. Equipment is already at max star level. Gold recieved instead.`\n`{base_equipment["value"]} gold`'
                    await db.update_user_gold(ctx.author.id, base_equipment["value"])
            else:
                await db.insert_equipment(ctx.author.id, drop_value, 'inventory')
        elif drop_type == Drop.EXP:
            exp_gained = drop_value + int(drop_value * total_stats['exp'] / 100)
            await db.update_user_exp(ctx.author.id, exp_gained)
            message_embed.description += f'`{exp_gained} exp ({drop_value} + {int(drop_value * total_stats["exp"] / 100)})`\n'
        await ctx.send(embed = message_embed)

    @commands.command(name = 'cave')
    async def cave(self, ctx, *, cave_name=''):
        cave_name = cave_name.title()
        user = await db.get_user(ctx.author.id)
        cave = Cave.from_cave_name(user['cave'])
        cave_quantity = cave.cave['current_quantity']
        user_level = User.exp_to_level(user['exp'])
        if cave_quantity == -1:
            cave_quantity = 'Infinite'
        message_embed = discord.Embed(title = 'Cave', color=discord.Color.dark_orange())
        to_cave = Cave.from_cave_name(cave_name)
        if cave_name and Cave.verify_cave(cave_name) and user_level >= to_cave.cave['level_requirement']:
            print('reach')
            await db.update_user_cave(ctx.author.id, cave_name)
            message_embed.description = f'You have switched to {cave_name}.'
        else:
            message_embed.description = f'**Current Cave**: `{user["cave"]}`\n**Remaining Mines:**`{cave_quantity}`\n**__Available Caves:__**\n' + Cave.list_caves_by_level(user_level)
        await ctx.send(embed = message_embed)

    @commands.command(name = 'stats')
    async def stats(self, ctx):
        message_embed = discord.Embed(title = f'{ctx.author}\'s Stats', color=discord.Color.dark_teal())
        user = await db.get_user(ctx.author.id)
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        ####TODO: MAKE SURE TO PASS IN ITEMS WHEN IT IS IMPLEMENTED!!!!!!####
        user_stats = '\n'.join([f'`{key}`: `{value}`' for key, value in User.get_total_stats(user, equipment_list).items()])
        stats = f'''
            `Level: {User.exp_to_level(user["exp"])}` \n
            `{User.get_exp_bar(user["exp"])}`\n
            `Total EXP: {user["exp"]}`\n
            `Gold: {user["gold"]}` \n
            **__Stats:__**\n
            {user_stats}
        '''
        message_embed.description = stats
        await ctx.send(embed = message_embed)

    @commands.command(name = 'equip')
    async def equip(self, ctx, *, equipment_name):
        equipment_name = equipment_name.title()
        message_embed = discord.Embed(title = 'Equip', color=discord.Color.from_rgb(245, 211, 201)) #peachy color
        user = await db.get_user(ctx.author.id)
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
        await ctx.send(embed = message_embed)

    @commands.command(name = 'gear')
    async def gear(self, ctx, *, equipment_name=''):
        equipment_name = equipment_name.title()
        message_embed = discord.Embed(title = 'Gear', color=discord.Color.from_rgb(245, 211, 201)) #peachy color
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        gear_str = User.get_equipment_stats_str(equipment_list, equipment_name)
        if equipment_name and gear_str:
            message_embed.description = gear_str
        else:
            message_embed.description = '**__Equipped Gear__**:\n' + User.get_equipped_gear_str(equipment_list)
        await ctx.send(embed = message_embed)

    @commands.command(name = 'inventory')
    async def inventory(self, ctx, *, equipment_name=''):
        equipment_name = equipment_name.title()
        message_embed = discord.Embed(title = 'Inventory', color=discord.Color.from_rgb(245, 211, 201)) #peachy color
        equipment_list = await db.get_equipment_for_user(ctx.author.id)
        equipment_str = User.get_equipment_stats_str(equipment_list, equipment_name)
        if equipment_name and equipment_str:
            message_embed.description = equipment_str
        else:
            message_embed.description = '**__Inventory__**:\n' + User.get_inventory_str(equipment_list)
        await ctx.send(embed = message_embed)

    @mine.error
    async def mine_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            message_embed = discord.Embed(color = discord.Color.dark_orange(), title = 'Mine', description = f'You are too tired to mine. {error}')
            await ctx.send(embed = message_embed)
        else:
            print(error)



def setup(client):
    client.add_cog(Mining(client))
