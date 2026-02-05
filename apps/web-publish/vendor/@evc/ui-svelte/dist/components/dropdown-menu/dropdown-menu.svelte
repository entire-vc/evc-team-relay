<script lang="ts">
  import { DropdownMenu as DropdownMenuPrimitive } from 'bits-ui';

  type Props = DropdownMenuPrimitive.RootProps;

  let { children, ...restProps }: Props = $props();
</script>

<DropdownMenuPrimitive.Root {...restProps}>
  {@render children?.()}
</DropdownMenuPrimitive.Root>
